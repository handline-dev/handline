// The hidden debug window (#/debug/terminals, opened from the bottom of Settings): every pane of the daemon's terminal host on the left,
// the chosen one's live terminal and what the daemon knows of it on the right. Reads debug.terminals again every few seconds.
import { useEffect, useState } from 'react';
import { useStore } from '@handline/app-react';
import { classify, sentence, useRead, useServes } from '@handline/app-data';
import { BytesTab } from '../BytesTab.tsx';
import type { CreateTerm } from '../terminal/core.ts';
import { Button } from '../ui/index.ts';
import { facts, flags, span, type DebugPane } from './paneFacts.ts';

const REFRESH_MS = 5000;

function PaneRow({ p, now, on, pick }: { p: DebugPane; now: number; on: boolean; pick: () => void }) {
  return (
    <li>
      <button
        type="button"
        data-pane={p.terminal}
        aria-current={on ? 'true' : undefined}
        className="flex w-full flex-col gap-0.5 rounded-ctl border-0 bg-transparent px-2 py-1.5 text-left hover:bg-raised aria-[current=true]:bg-raised"
        onClick={pick}
      >
        <span className="flex items-center gap-2 text-body text-ink">
          <span className="font-mono text-label text-ink-3">
            {p.session}:{p.window} {p.terminal}
          </span>
          <span className="truncate">{p.agent ?? p.window_name}</span>
          <span className="ml-auto text-label text-ink-3">{p.idle_s === null ? '-' : span(p.idle_s)}</span>
        </span>
        <span className="flex flex-wrap gap-1">
          {flags(p, now).map((f) => (
            <span
              key={f.word}
              data-flag={f.word}
              title={f.why}
              className={`rounded-[4px] px-1 text-label ${f.bad ? 'bg-[color-mix(in_srgb,var(--bad)_14%,transparent)] text-bad' : 'text-ink-2'}`}
            >
              {f.word}
            </span>
          ))}
        </span>
      </button>
    </li>
  );
}

function Facts({ p, now }: { p: DebugPane; now: number }) {
  return (
    <dl className="debug-facts m-0 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-0.5 border-b border-line px-3 py-2 text-body">
      {facts(p, now).map(([k, v]) => (
        <div key={k} className="contents" data-fact={k}>
          <dt className="text-ink-3">{k}</dt>
          <dd className="m-0 font-mono text-label [overflow-wrap:anywhere] text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** `create` makes the terminal; a test hands in a headless one. */
export function DebugTerminals({ create }: { create?: CreateTerm }) {
  const store = useStore();
  const { served } = useServes('debug.terminals');
  const q = useRead('debug.terminals', {}, { refetchMs: REFRESH_MS });
  const [chosen, setChosen] = useState('');
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    document.title = 'Terminals (debug)';
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const panes = q.data?.panes ?? [];
  const pane = panes.find((p) => p.terminal === chosen);
  const bytes = !!store.client.hello?.capabilities.terminal_format?.includes('bytes');
  return (
    <div className="debug-terminals grid h-screen grid-cols-[320px_minmax(0,1fr)] bg-canvas" aria-label="Terminals (debug)">
      <aside className="flex min-h-0 flex-col border-r border-line bg-panel">
        <header className="flex items-center gap-2 px-3 py-2">
          <h2 className="m-0 text-body font-semibold text-ink">Terminals</h2>
          <span className="text-label text-ink-3">
            {q.data ? `${q.data.host} · ${panes.length} panes` : ''}
          </span>
          <Button variant="outline" size="sm" label="Refresh" className="ml-auto" onClick={() => void q.refetch()} />
        </header>
        {!served ? (
          <p className="muted pad">This daemon does not serve debug.terminals.</p>
        ) : q.isError ? (
          <p className="pad text-bad" role="alert">
            {sentence(classify(q.error))}
          </p>
        ) : (
          <ul className="m-0 min-h-0 flex-1 list-none overflow-auto p-1">
            {panes.map((p) => (
              <PaneRow key={p.terminal} p={p} now={now} on={p.terminal === chosen} pick={() => setChosen(p.terminal)} />
            ))}
          </ul>
        )}
      </aside>
      <main className="flex min-h-0 min-w-0 flex-col">
        {!pane ? (
          <div className="empty big">
            <strong>Pick a pane</strong>
            <p>Choose one in the list to watch it.</p>
          </div>
        ) : (
          <>
            <Facts p={pane} now={now} />
            {bytes ? (
              <BytesTab
                key={pane.terminal}
                agent={pane.agent ?? ''}
                detail={{ terminal: pane.terminal }}
                handle={pane.agent ? undefined : pane.terminal}
                create={create}
              />
            ) : (
              <p className="muted pad">This daemon does not serve the bytes terminal, which the debug window needs.</p>
            )}
          </>
        )}
      </main>
    </div>
  );
}
