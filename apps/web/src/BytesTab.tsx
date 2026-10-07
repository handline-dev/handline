// Terminal tab on the `bytes` channel: xterm.js draws the host's own escape codes, colour and cursor included. Read-only until the
// person holds the input; the text-grid tab is the fallback (TerminalTab). See docs/terminal-pane.md.
import { useEffect, useRef, useState } from 'react';
import { toBase64, type AgentDetail, type ByteListener, type ByteTerminalView, type ByteViewState } from '@handline/client';
import { controlsOf } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { useWrite } from '@handline/app-data';
import { useInputHold } from './useInputHold.ts';
import { Chips, TermBar } from './TermBar.tsx';
import { keep, reclaim } from './terminal/keep.ts';
import {
  KEY_BYTES,
  MAX_UNWRITTEN,
  clampGrid,
  fontFor,
  guard,
  isReport,
  markerLabel,
  paint,
  type CreateTerm,
  type PaneTerm,
} from './terminal/core.ts';

const RESIZE_DELAY_MS = 150;
const encode = new TextEncoder();
const SKELETON_DELAY_MS = 150;
const SKELETON_MIN_MS = 300;
const SKELETON_ROWS = [64, 82, 48, 90, 36, 72, 56, 28]; // percent of the width, in the shape of a screen of text
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
  const live = useRef<{ view: ByteTerminalView; pane: PaneTerm } | null>(null);
  const [state, setState] = useState<ByteViewState>('attaching');
  const [failure, setFailure] = useState('');
  const [title, setTitle] = useState('');
  const [everLive, setEverLive] = useState(false);
  const [attempt, setAttempt] = useState(0); // Retry runs the effect again
  const c = controlsOf(detail, store.me);
  const releaseWrite = useWrite('input.release', agent);
  const releaseInput = useRef(releaseWrite.mutateAsync);
  releaseInput.current = releaseWrite.mutateAsync; // the detach cleanup reads the current one
  const sendKey = useWrite('terminal.input', agent);
  const holdApi = useInputHold(agent, detail);
  const hold = useRef(holdApi);
  hold.current = holdApi;
  const queued = useRef('');
  const holding = useRef(c.typing);
  holding.current = c.typing; // handlers read the current value without re-binding
  const hasTerminal = !!detail.terminal;

  useEffect(() => {
    if (!hasTerminal) return;
    let cancelled = false,
      timer: ReturnType<typeof setTimeout> | undefined,
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
      const layout = () => {
        const v = live.current?.view;
        if (!v) return;
        if (!holding.current) return pane.setFontSize(fontFor(el.clientWidth, v.cols));
        clearTimeout(timer); // holding: ask the host for the grid this pane fits, once the size stops changing
        timer = setTimeout(() => {
          const g = pane.propose();
          if (g && (g.cols !== v.cols || g.rows !== v.rows)) {
            const { cols, rows } = clampGrid(g.cols, g.rows);
            void store.client.bytes.resize(v, cols, rows).catch(() => {});
          }
        }, RESIZE_DELAY_MS);
      };
      const typed = pane.term.onData((d) => {
        if (isReport(d) && !holding.current) return; // the terminal's own answers are never typing
        if (holding.current && !queued.current) {
          hold.current.touch();
          if (live.current) void store.client.bytes.input(live.current.view, encode.encode(d)).catch(() => {});
          return;
        }
        queued.current += d; // the first key takes the input; what was typed meanwhile goes out in order after
        if (queued.current !== d) return;
        void hold.current.ensure().then((ok) => {
          const out = queued.current;
          queued.current = '';
          if (ok && out && live.current) void store.client.bytes.input(live.current.view, encode.encode(out)).catch(() => {});
        });
      });
      const watch = new ResizeObserver(layout);
      watch.observe(el);
      let off = () => {};
      const onEvent: ByteListener = (e, v) => {
        if (e.type === 'marker') return pane.mark(markerLabel(e.marker));
        if (e.type === 'state') {
          setState(v.state);
          setFailure(v.failure);
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
        clearTimeout(timer);
        watch.disconnect();
        typed.dispose();
        off();
        if (live.current) {
          if (holding.current) void releaseInput.current({ agent }).catch(() => {}); // a detach lets go of the input this device holds
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
  }, [store, agent, hasTerminal, create, attempt]);

  useEffect(() => {
    // Releasing the input hands the grid back to the host: scale the font to it again. (Holding resizes when the pane's size changes.)
    const v = live.current;
    if (v && !c.typing) v.pane.setFontSize(fontFor(host.current?.clientWidth ?? 0, v.view.cols));
  }, [c.typing]);

  /** A chip is typing, so it takes the input first, then sends the bytes the key stands for. */
  const press = async (key: string) => {
    const v = live.current?.view;
    if (!v || !(await holdApi.ensure())) return;
    await sendKey.mutateAsync({ terminal: v.terminal, data: toBase64(encode.encode(KEY_BYTES[key] ?? key)) }).catch(() => {});
  };
  const skeleton = useSkeleton(hasTerminal && state === 'attaching' && !everLive);
  const note = !hasTerminal ? 'No terminal. Start the agent to open one.' : state === 'failed' ? failure : NOTE[state];

  return (
    <div className="terminal-tab">
      <TermBar agent={agent} detail={detail} />
      <div className={`terminal xterm-pane${state === 'stale' || !hasTerminal ? ' dim' : ''}`} title={title}>
        <div className="xterm-host" ref={host} aria-label={`Terminal of ${agent}`} />
        {skeleton && (
          <div className="term-skeleton" aria-hidden="true">
            {SKELETON_ROWS.map((w, i) => (
              <div key={i} className="term-skeleton-row" style={{ width: `${w}%` }} />
            ))}
          </div>
        )}
        <div className="grid-status">
          {note}
          {state === 'failed' && (
            <button className="btn" onClick={() => setAttempt((n) => n + 1)}>
              Retry
            </button>
          )}
        </div>
      </div>
      <Chips enabled={detail.session_open && (c.typing || c.take)} onPress={(key) => void press(key)} />
    </div>
  );
}
