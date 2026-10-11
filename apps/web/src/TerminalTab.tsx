// Terminal tab: the agent session's live text grid, watch-only. Attached only while this tab is rendered; Start, Stop and Resume are in the sidebar.
import { useEffect, useRef, useState } from 'react';
import type { AgentDetail, TerminalView } from '@handline/client';
import { useStore } from '@handline/app-react';
import { Skeleton } from '@handline/app-data';
import { fontFor } from './terminal/core.ts';
import { drawRows } from './terminal/spans.ts';

export function TerminalTab({ agent, detail }: { agent: string; detail: AgentDetail }) {
  const store = useStore();
  const grid = useRef<HTMLPreElement>(null);
  const view = useRef<TerminalView | null>(null);
  const [status, setStatus] = useState('');
  const [attaching, setAttaching] = useState(false); // the grid's skeleton shows until the terminal is attached or refused
  const hasTerminal = !!detail.terminal;

  useEffect(() => {
    const g = grid.current!;
    if (!hasTerminal) {
      g.textContent = '';
      setAttaching(false);
      setStatus('No terminal. Start the agent to open one.');
      return;
    }
    let cancelled = false,
      off: (() => void) | null = null,
      frame = 0;
    setAttaching(true);
    /** The host's columns scaled into the pane's width. */
    const fit = () => {
      if (view.current) g.style.fontSize = `${fontFor(g.clientWidth - 20, view.current.cols)}px`;
    };
    const watch = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(fit);
    watch?.observe(g);
    const paint = (stick = false) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!view.current) return;
        const atBottom = stick || g.scrollHeight - g.scrollTop - g.clientHeight < 4,
          before = g.scrollHeight;
        fit();
        g.replaceChildren(drawRows(document, view.current.lines()));
        if (atBottom) g.scrollTop = g.scrollHeight;
        else g.scrollTop += g.scrollHeight - before; // older lines arrived above
      });
    };
    store.client.attachTerminal(agent).then(
      (v) => {
        if (cancelled) {
          void store.client.detachTerminal(agent).catch(() => {});
          return;
        }
        view.current = v;
        setAttaching(false);
        setStatus('');
        off = v.subscribe(() => paint());
        paint(true);
      },
      (e) => {
        if (cancelled) return;
        setAttaching(false);
        setStatus((e as { reason?: string }).reason ?? (e as Error).message);
      },
    );
    return () => {
      cancelled = true;
      watch?.disconnect();
      off?.();
      cancelAnimationFrame(frame);
      if (view.current) {
        view.current = null;
        void store.client.detachTerminal(agent).catch(() => {});
      }
    };
  }, [store, agent, hasTerminal]);

  return (
    <div className="terminal-tab flex min-h-0 flex-col">
      <div className="terminal">
        <pre
          className="grid"
          ref={grid}
          aria-label={`Terminal of ${agent}`}
          onScroll={(e) => {
            const v = view.current;
            if (e.currentTarget.scrollTop === 0 && v?.historyMore) void store.client.loadOlder(v).catch(() => {});
          }}
        />
        <div className="grid-skeleton">
          <Skeleton shape="terminalGrid" loading={attaching} label="the terminal" />
        </div>
        <div className="grid-status">{status}</div>
      </div>
    </div>
  );
}
