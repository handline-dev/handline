// Terminal tab on the `bytes` channel: xterm.js draws the host's own escape codes, colour and cursor included. Watch-only; the
// text-grid tab is the fallback (TerminalTab). See docs/terminal-pane.md.
import { useEffect, useRef, useState } from 'react';
import { type AgentDetail, type ByteListener, type ByteTerminalView, type ByteViewState } from '@handline/client';
import { useStore } from '@handline/app-react';
import { keep, reclaim } from './terminal/keep.ts';
import { MAX_UNWRITTEN, fontFor, guard, markerLabel, paint, type CreateTerm, type PaneTerm } from './terminal/core.ts';
import { Button } from './ui/index.ts';

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
      const layout = () => {
        const v = live.current?.view;
        if (v) pane.setFontSize(fontFor(el.clientWidth, v.cols));
      };
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
        watch.disconnect();
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
  }, [store, agent, hasTerminal, create, attempt]);

  const skeleton = useSkeleton(hasTerminal && state === 'attaching' && !everLive);
  const note = !hasTerminal ? 'No terminal. Start the agent to open one.' : state === 'failed' ? failure : NOTE[state];

  return (
    <div className="terminal-tab flex min-h-0 flex-col">
      <div className={`terminal xterm-pane relative flex min-h-0 flex-1 flex-col bg-term-bg${state === 'stale' || !hasTerminal ? ' dim [&_.xterm-host]:opacity-55' : ''}`} title={title}>
        <div className="xterm-host min-h-0 flex-1 overflow-hidden px-2.5 py-2 [&>div]:h-full" ref={host} aria-label={`Terminal of ${agent}`} />
        {skeleton && (
          <div className="term-skeleton absolute inset-0 flex flex-col gap-2 bg-term-bg px-2.5 py-3" aria-hidden="true">
            {SKELETON_ROWS.map((w, i) => (
              <div key={i} className="term-skeleton-row h-[13px] animate-[term-pulse_1.2s_ease-in-out_infinite] rounded-[3px] bg-line opacity-60 motion-reduce:animate-none" style={{ width: `${w}%` }} />
            ))}
          </div>
        )}
        <div className="grid-status px-2.5 pb-1.5 text-[12px] text-wait empty:hidden">
          {note}
          {state === 'failed' && (
            <Button variant="outline" size="sm" label="Retry" onClick={() => setAttempt((n) => n + 1)} />
          )}
        </div>
      </div>
    </div>
  );
}
