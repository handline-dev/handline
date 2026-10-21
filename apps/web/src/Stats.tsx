// The Stats section of the Usage page (#/accounts, #/stats), under the account list. Two tabs: Headroom (stats.headroom, when the daemon serves
// it), and Activity: the window's four counts and how long each agent worked (stats.get), the cost per day below them (stats.outlook). The
// previous window's figures stay on screen until the new ones arrive.
import { useEffect, useState } from 'react';
import { agentTime, dateTime, span } from '@handline/app-core';
import type { Stats } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useServes, useStats } from '@handline/app-data';
import { HeadroomTab } from '#src/Headroom.tsx';
import { OutlookSection } from '#src/Outlook.tsx';
import { ChartCard, ChartTitle, Icon, SectionHead, Segmented, type IconName } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

const WINDOWS = ['24h', '7d'] as const;
type Window = (typeof WINDOWS)[number];
const WORD: Record<Window, string> = { '24h': '24 h', '7d': '7 d' };

const TILES: [string, keyof Pick<Stats, 'tasks_done' | 'moves' | 'handoffs' | 'blocks'>, IconName][] = [
  ['tasks done', 'tasks_done', 'done'],
  ['moves', 'moves', 'session'],
  ['handoffs', 'handoffs', 'reply'],
  ['blocks', 'blocks', 'blocked'],
];

const TILE = 'stat-tile flex flex-col gap-1 rounded-card bg-surface px-4 py-3 [&_svg]:text-ink-3 data-[warn]:[&_svg]:text-bad';

/** Icon, count, name: the icon says what is counted, so the name can stay small. */
function Tiles({ s }: { s: Stats }) {
  return (
    <div className="stat-tiles grid grid-cols-[repeat(4,1fr)] gap-3 max-sm:grid-cols-[1fr_1fr]">
      {TILES.map(([label, key, icon]) => (
        <div key={label} className={TILE} data-tile={label} data-warn={key === 'blocks' && s[key] > 0 ? '' : undefined}>
          <Icon name={icon} size={20} label={label} />
          <strong className="text-page font-semibold tabular-nums">{s[key]}</strong>
          <span className="muted">{label}</span>
        </div>
      ))}
    </div>
  );
}

function AgentTime({ s }: { s: Stats }) {
  const { rows, idle } = agentTime(s.working_seconds);
  const top = rows[0]?.seconds ?? 1;
  return (
    <ChartCard aria-label="Agent time">
      <ChartTitle>Agent time</ChartTitle>
      {rows.length ? (
        rows.map((r) => (
          <div key={r.agent} className="bar-row my-1 grid grid-cols-[minmax(70px,40%)_1fr_auto] items-center gap-2" data-agent={r.agent}>
            <span className="bar-name truncate">{r.agent}</span>
            <span className="bar h-2 overflow-hidden rounded-chip bg-raised">
              <span className="bar-fill accent block h-full bg-ink-2" style={{ width: `${(r.seconds / top) * 100}%` }} />
            </span>
            <span className="bar-value font-mono text-label">{span(r.seconds * 1000)}</span>
          </div>
        ))
      ) : (
        <p className="muted">Nothing recorded in this window.</p>
      )}
      {idle ? (
        <p className="muted">
          {idle} idle {idle === 1 ? 'agent' : 'agents'}
        </p>
      ) : null}
    </ChartCard>
  );
}

type Tab = 'headroom' | 'activity';

export function StatsSection() {
  const [tab, setTab] = useState<Tab>('headroom');
  const headroom = useSelector((s) => s.can('stats.headroom'));
  const shown: Tab = headroom ? tab : 'activity';
  const [win, setWin] = useState<Window>('24h');
  const q = useStats(win);
  const { served } = useServes('stats.get');
  const outlook = useSelector((s) => s.can('stats.outlook'));
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
    <section className="stats mt-6" aria-label="Stats">
      <div className="stats-head mb-2 flex items-center [&_.ui-seg]:ml-auto">
        <SectionHead className="!mb-0">Stats</SectionHead>
        {headroom ? (
          <Segmented
            label="Stats tab"
            value={shown}
            onChange={setTab}
            options={[
              { value: 'headroom', label: 'Headroom' },
              { value: 'activity', label: 'Activity' },
            ]}
          />
        ) : null}
        {shown === 'activity' ? (
          <Segmented label="Window" value={win} onChange={setWin} options={WINDOWS.map((w) => ({ value: w, label: WORD[w] }))} />
        ) : null}
      </div>
      {shown === 'headroom' ? <HeadroomTab /> : null}
      {shown === 'activity' ? (
        <View q={q} served={served} shape="stats" what="stats">
          {s ? (
            <>
              <p className="muted" data-span>
                {dateTime(s.since)} to {dateTime(s.until)}
              </p>
              <Tiles s={s} />
              <div className="charts mt-3 flex flex-col gap-3">
                <AgentTime s={s} />
              </div>
            </>
          ) : null}
        </View>
      ) : null}
      {shown === 'activity' && outlook ? <OutlookSection /> : null}
    </section>
  );
}
