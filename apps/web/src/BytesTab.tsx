// Terminal tab on the `bytes` channel: xterm.js draws the host's own escape codes, colour and cursor included. Watch-only; the
// text-grid tab is the fallback (TerminalTab). See docs/terminal-pane.md.
import { useEffect, useRef, useState } from 'react';
import { paneKey, type AgentDetail, type ByteListener, type ByteTerminalView, type ByteViewState } from '@handline/client';
import { useStore } from '@handline/app-react';
import { useWrite } from '@handline/app-data';
import { keep, reclaim } from './terminal/keep.ts';
import {
  DEFAULT_FONT,
  MAX_UNWRITTEN,
  fontFor,
  gridToSend,
  guard,
  isEscapeChord,
  keySender,
  markerLabel,
  paint,
  type CreateTerm,
  type PaneTerm,
} from './terminal/core.ts';
import { Button } from './ui/index.ts';

const SKELETON_DELAY_MS = 150;
const SKELETON_MIN_MS = 300;
const SKELETON_ROWS = [64, 82, 48, 90, 36, 72, 56, 28]; // percent of the width, in the shape of a screen of text
const RESIZE_MS = 150;
const utf8 = new TextEncoder();
const NOTE: Record<ByteViewState, string> = { attaching: '', live: '', stale: 'Reconnecting…', failed: '' };

/** The skeleton of the first load: shown after a short delay, then kept for a minimum so it does not flash. */
function useSkeleton(loading: boolean): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (loading) {
      const t = setTimeout(() => setShown(true), SKELETON_DELAY_MS);
      return () => clearTimeout(t);
    }
    if (!shown) return;
    const t = setTimeout(() => setShown(false), SKELETON_MIN_MS);
    return () => clearTimeout(t);
  }, [loading, shown]);
  return shown;
}

const reasonOf = (e: unknown) => (e as { reason?: string }).reason ?? (e as Error).message;

/**
 * `handle` attaches by terminal handle instead of by agent (the debug window). With no agent it is watch-only: no agent, no input holder.
 */
export function BytesTab({
  agent,
  detail,
  create,
  handle,
}: {
  agent: string;
  detail: Pick<AgentDetail, 'terminal'>;
  create?: CreateTerm;
  handle?: string;
}) {
  const store = useStore();
  const host = useRef<HTMLDivElement>(null);
  const takeInput = useWrite('input.take', agent);
  const releaseInput = useWrite('input.release', agent);
  const { mutate: take } = takeInput;
  const { mutate: release } = releaseInput;
  const live = useRef<{ view: ByteTerminalView; pane: PaneTerm } | null>(null);
  const [state, setState] = useState<ByteViewState>('attaching');
  const [failure, setFailure] = useState('');
  const [title, setTitle] = useState('');
  const [everLive, setEverLive] = useState(false);
  const [holds, setHolds] = useState(false); // this device holds the input: keys go in and the pane sets the size
  const [refused, setRefused] = useState('');
  const [heldBy, setHeldBy] = useState(''); // someone else holds the input: a click cannot take it
  const [attempt, setAttempt] = useState(0); // Retry runs the effect again
  const hasTerminal = !!detail.terminal;
  const key = handle ? paneKey(handle) : agent; // what the view and the kept screen are filed under
  const watchOnly = !agent;

  useEffect(() => {
    if (!hasTerminal) return;
    let cancelled = false,
      cleanup = () => {};
    void (async () => {
      const make = create ?? (await import('./terminal/xterm.ts')).createXterm;
      if (cancelled) return;
      const el = host.current!;
      const back = reclaim(key); // a quick switch back shows the last screen at once; the new snapshot repaints it
      const stage = back?.stage ?? el.appendChild(document.createElement('div'));
      if (back) el.appendChild(stage);
      const title = back?.title ?? { to: setTitle };
      title.to = setTitle;
      const pane = back?.pane ?? make(stage);
      const quiet = back?.quiet ?? guard(pane.term, (t) => title.to(t));
      let unwritten = 0;
      let wasLive = false;
      let holding = false,
        otherHolds = false, // another person holds the input: they size the terminal and this pane only scales its font
        timer: ReturnType<typeof setTimeout> | undefined;
      /** The pane sizes the host unless another person holds the input: the one who holds, or anyone while nobody does. */
      const sizes = () => !watchOnly && (holding || !otherHolds);
      const sendSize = () => {
        const v = live.current?.view;
        const g = v && v.state === 'live' && sizes() ? gridToSend(pane.propose(), v) : null;
        if (v && g) void store.client.bytes.resize(v, g.cols, g.rows).catch(() => {}); // a refused size leaves the grid as it is
      };
      /** The window changed (or the pane just went live): ask the host for the grid that fits it, after a short debounce. */
      const layout = () => {
        const v = live.current?.view;
        if (!v) return;
        if (!sizes()) return pane.setFontSize(fontFor(el.clientWidth, v.cols));
        clearTimeout(timer);
        timer = setTimeout(sendSize, RESIZE_MS);
        repaint();
      };
      /** The host's grid changed, whoever changed it. Never asks for a size: two panes of different sizes would trade sizes for ever. */
      const repaint = () => {
        const v = live.current?.view;
        if (!v) return;
        const fits = sizes() && gridToSend(pane.propose(), v) === null;
        pane.setFontSize(fits ? DEFAULT_FONT : fontFor(el.clientWidth, v.cols));
      };
      const watch = new ResizeObserver(layout);
      const sendKeys = keySender(
        (data) => (live.current ? store.client.bytes.input(live.current.view, data) : Promise.resolve()), // keys queued past a detach are dropped
        (e) => setRefused(e.message),
      );
      const typing = pane.term.onData((d) => {
        if (holding && live.current) sendKeys(utf8.encode(d));
      });
      const grab = () => {
        const v = live.current?.view;
        if (!v || watchOnly || holding || v.state !== 'live' || v.holders.some((h) => h.kind !== 'daemon')) return;
        take({ agent }); // a refusal is reported by the write
      };
      /** The one key the app keeps: the terminal lets go of the keyboard and the hold is released. */
      const leave = (e: KeyboardEvent) => {
        if (!isEscapeChord(e)) return;
        pane.blur?.();
        if (holding) release({ agent });
      };
      stage.addEventListener('mousedown', grab);
      stage.addEventListener('focusin', grab);
      stage.addEventListener('keydown', leave);
      watch.observe(el);
      let off = () => {};
      /** The view's state, holders included: what the person sees, and whether this pane sizes the host (on going live and on any change). */
      const settle = (v: ByteTerminalView) => {
        setState(v.state);
        setFailure(v.failure);
        const now = v.state === 'live' && v.holders.some((h) => h.kind !== 'daemon' && h.name === store.me);
        const other = v.state === 'live' && v.holders.some((h) => h.kind !== 'daemon' && h.name !== store.me);
        setHeldBy(v.holders.find((h) => h.kind !== 'daemon' && h.name !== store.me)?.name ?? '');
        const wentLive = v.state === 'live' && !wasLive;
        wasLive = v.state === 'live';
        if (now !== holding || other !== otherHolds || wentLive) {
          if (now !== holding) setRefused('');
          holding = now;
          otherHolds = other;
          setHolds(now);
          layout();
        }
        if (v.state === 'live') setEverLive(true);
      };
      const onEvent: ByteListener = (e, v) => {
        if (e.type === 'marker') return pane.mark(markerLabel(e.marker));
        if (e.type === 'state') return void settle(v);
        if (e.type === 'write') unwritten += e.data.length;
        paint(pane.term, v, e, (n) => (unwritten -= n));
        if (e.type === 'reset') repaint();
        if (unwritten > MAX_UNWRITTEN) {
          unwritten = 0;
          void store.client.bytes.resync(v); // a stream the screen cannot keep up with is dropped and taken again
        }
      };
      try {
        const opened = await (handle ? store.client.bytes.openPane(handle, onEvent) : store.client.bytes.open(agent, onEvent));
        off = opened.off;
        live.current = { view: opened.view, pane };
        settle(opened.view);
        layout(); // a state notice that came while opening found no view yet
      } catch (e) {
        setState('failed');
        setFailure((e as { reason?: string }).reason ?? (e as Error).message);
      }
      cleanup = () => {
        watch.disconnect();
        clearTimeout(timer);
        typing.dispose();
        stage.removeEventListener('mousedown', grab);
        stage.removeEventListener('focusin', grab);
        stage.removeEventListener('keydown', leave);
        off();
        if (live.current) {
          void store.client.bytes.close(key).catch(() => {});
        }
        live.current = null;
        keep(key, { stage, pane, quiet, title });
      };
      if (cancelled) cleanup();
    })();
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [store, agent, key, handle, watchOnly, hasTerminal, create, attempt, take, release]);

  const skeleton = useSkeleton(hasTerminal && state === 'attaching' && !everLive);
  const note = !hasTerminal ? 'No terminal. Start the agent to open one.' : state === 'failed' ? failure : NOTE[state];

  return (
    <div className="terminal-tab flex min-h-0 flex-col">
      <div
        className={`terminal xterm-pane relative flex min-h-0 flex-1 flex-col bg-term-bg${state === 'stale' || !hasTerminal ? ' dim [&_.xterm-host]:opacity-55' : ''}`}
        title={title}
      >
        <div
          className="xterm-host min-h-0 flex-1 overflow-hidden px-2.5 py-2 [&>div]:h-full"
          ref={host}
          aria-label={`Terminal of ${agent}`}
        />
        {skeleton && (
          <div className="term-skeleton absolute inset-0 flex flex-col gap-2 bg-term-bg px-2.5 py-3" aria-hidden="true">
            {SKELETON_ROWS.map((w, i) => (
              <div
                key={i}
                className="term-skeleton-row h-[13px] animate-[term-pulse_1.2s_ease-in-out_infinite] rounded-[3px] bg-line opacity-60 motion-reduce:animate-none"
                style={{ width: `${w}%` }}
              />
            ))}
          </div>
        )}
        <div className="grid-status px-2.5 pb-1.5 text-[12px] text-wait empty:hidden">
          {note}
          {state === 'live' &&
            (holds ? (
              <>
                {'You hold the input. Ctrl+Shift+E hands the keys back to the app. '}
                <Button variant="outline" size="sm" label="Release" className="text-term-ink" onClick={() => release({ agent })} />
              </>
            ) : (
              !heldBy && !takeInput.error && (watchOnly ? 'Watch only: no agent has this pane, so it takes no keys. ' : 'Click the terminal to type in it. ')
            ))}
          {heldBy && !holds && `The input is held by ${heldBy}. `}
          {refused || (takeInput.error ? `Could not take the input: ${reasonOf(takeInput.error)}` : '')}
          {state === 'failed' && <Button variant="outline" size="sm" label="Retry" onClick={() => setAttempt((n) => n + 1)} />}
        </div>
      </div>
    </div>
  );
}
