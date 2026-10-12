// Terminal tab on the `bytes` channel: xterm.js draws the host's own escape codes, colour and cursor included. Watch-only; the
// text-grid tab is the fallback (TerminalTab). See docs/terminal-pane.md.
import { useEffect, useRef, useState } from 'react';
import { type AgentDetail, type ByteListener, type ByteTerminalView, type ByteViewState } from '@handline/client';
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

export function BytesTab({ agent, detail, create }: { agent: string; detail: AgentDetail; create?: CreateTerm }) {
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
  const [attempt, setAttempt] = useState(0); // Retry runs the effect again
  const hasTerminal = !!detail.terminal;

  useEffect(() => {
    if (!hasTerminal) return;
    let cancelled = false,
      cleanup = () => {};
    void (async () => {
      const make = create ?? (await import('./terminal/xterm.ts')).createXterm;
      if (cancelled) return;
      const el = host.current!;
      const back = reclaim(agent); // a quick switch back shows the last screen at once; the new snapshot repaints it
      const stage = back?.stage ?? el.appendChild(document.createElement('div'));
      if (back) el.appendChild(stage);
      const title = back?.title ?? { to: setTitle };
      title.to = setTitle;
      const pane = back?.pane ?? make(stage);
      const quiet = back?.quiet ?? guard(pane.term, (t) => title.to(t));
      let unwritten = 0;
      let holding = false,
        timer: ReturnType<typeof setTimeout> | undefined;
      /** Holding: the pane asks the host for the grid that fits it. Not holding: the font is scaled to the host's grid. */
      const sendSize = () => {
        const v = live.current?.view;
        const g = v && gridToSend(pane.propose(), v);
        if (v && g) void store.client.bytes.resize(v, g.cols, g.rows).catch(() => {}); // a refused size leaves the grid as it is
      };
      const layout = () => {
        const v = live.current?.view;
        if (!v) return;
        if (!holding) return pane.setFontSize(fontFor(el.clientWidth, v.cols));
        pane.setFontSize(DEFAULT_FONT);
        clearTimeout(timer);
        timer = setTimeout(sendSize, RESIZE_MS);
      };
      const watch = new ResizeObserver(layout);
      const sendKeys = keySender(
        (data) => store.client.bytes.input(live.current!.view, data),
        (e) => setRefused(e.message),
      );
      const typing = pane.term.onData((d) => {
        if (holding && live.current) sendKeys(utf8.encode(d));
      });
      const grab = () => {
        const v = live.current?.view;
        if (!v || holding || v.state !== 'live' || v.holders.some((h) => h.kind !== 'daemon')) return;
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
      const onEvent: ByteListener = (e, v) => {
        if (e.type === 'marker') return pane.mark(markerLabel(e.marker));
        if (e.type === 'state') {
          setState(v.state);
          setFailure(v.failure);
          const now = v.state === 'live' && v.holders.some((h) => h.kind !== 'daemon' && h.name === store.me);
          if (now !== holding) {
            holding = now;
            setHolds(now);
            setRefused('');
            layout();
          }
          if (v.state === 'live') setEverLive(true);
          return;
        }
        if (e.type === 'write') unwritten += e.data.length;
        paint(pane.term, v, e, (n) => (unwritten -= n));
        if (e.type === 'reset') layout();
        if (unwritten > MAX_UNWRITTEN) {
          unwritten = 0;
          void store.client.bytes.resync(v); // a stream the screen cannot keep up with is dropped and taken again
        }
      };
      try {
        const opened = await store.client.bytes.open(agent, onEvent);
        off = opened.off;
        live.current = { view: opened.view, pane };
        setState(opened.view.state);
        if (opened.view.state === 'live') setEverLive(true);
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
          void store.client.bytes.close(agent).catch(() => {});
        }
        live.current = null;
        keep(agent, { stage, pane, quiet, title });
      };
      if (cancelled) cleanup();
    })();
    return () => {
      cancelled = true;
      cleanup();
    };
  }, [store, agent, hasTerminal, create, attempt, take, release]);

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
              'Click the terminal to type in it. '
            ))}
          {refused}
          {state === 'failed' && <Button variant="outline" size="sm" label="Retry" onClick={() => setAttempt((n) => n + 1)} />}
        </div>
      </div>
    </div>
  );
}
