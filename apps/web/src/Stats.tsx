// The Stats section of the Usage page (#/stats), under the account lines: who used how much, from stats.get. The previous window's figures stay on screen until the new ones arrive.
import { useEffect, useState } from 'react';
import { SPENT_HEAD, agentUse, spentText } from '@handline/app-core';
import type { Stats } from '@handline/client';
import { useServes, useStats } from '@handline/app-data';
import { Segmented } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

const WINDOWS = ['24h', '7d'] as const;
type Window = (typeof WINDOWS)[number];
const WORD: Record<Window, string> = { '24h': '24 h', '7d': '7 d' };

const HEAD = ['Agent', 'Account'];

/** Who spent the week, as a plain table: the spent text of each agent with a thin bar behind it. */
function WhoUsedIt({ s }: { s: Stats }) {
  const rows = agentUse(s.use);
  return rows.length ? (
    <table className="who-used w-full border-collapse text-sm" aria-label="Who used it">
      <thead>
        <tr className="muted text-left text-xs">
          {HEAD.map((h) => (
            <th key={h} className="py-1 pr-4 font-normal">
              {h}
            </th>
          ))}
          <th className="py-1 font-normal" colSpan={2}>
            {SPENT_HEAD}
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} data-agent={r.agent}>
            <td className="py-1 pr-4">{r.agent}</td>
            <td className="muted py-1 pr-4">{r.account}</td>
            <td className="w-40 py-1 pr-3 font-mono whitespace-nowrap">{spentText(r)}</td>
            <td className="py-1">
              <span className="bar block h-1.5 overflow-hidden rounded-sm bg-raised">
                <span className="bar-fill block h-full bg-ink-2" style={{ width: `${r.fill * 100}%` }} />
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <p className="muted">Nothing recorded in this window.</p>
  );
}

export function StatsSection() {
  const [win, setWin] = useState<Window>('24h');
  const q = useStats(win);
  const { served } = useServes('stats.get');
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'r' || e.metaKey || e.ctrlKey || e.altKey || (e.target as HTMLElement | null)?.closest?.('input, textarea, select'))
        return;
      setWin((w) => (w === '24h' ? '7d' : '24h'));
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);
  const s = q.data;
  return (
    <section className="stats" aria-label="Stats">
      <div className="stats-head mb-2 flex items-center [&_.ui-seg]:ml-auto">
        <h3 className="m-0 text-lg font-semibold">Who used it</h3>
        <Segmented label="Window" value={win} onChange={setWin} options={WINDOWS.map((w) => ({ value: w, label: WORD[w] }))} />
      </div>
      <View q={q} served={served} shape="stats" what="stats">
        {s ? (
          <>
            <WhoUsedIt s={s} />
          </>
        ) : null}
      </View>
    </section>
  );
}
