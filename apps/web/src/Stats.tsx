// The Stats page (#/stats): the window's four counts, how long each agent worked, from stats.get; the usage outlook below them when the daemon
// serves it. The accounts' use is the Accounts page (a link here). The previous window's figures stay on screen until the new ones arrive.
import { useEffect, useState } from 'react';
import { agentTime, duration } from '@handline/app-core';
import type { Stats } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useServes, useStats } from '@handline/app-data';
import { CHART, CHART_TITLE, PAGE, PAGE_HEAD, PAGE_TITLE } from './pageStyle.ts';
import { OutlookSection } from './Outlook.tsx';
import { Icon, Segmented, type IconName } from './ui/index.ts';
import { View } from './View.tsx';

const WINDOWS = ['24h', '7d'] as const;
type Window = (typeof WINDOWS)[number];
const WORD: Record<Window, string> = { '24h': '24 h', '7d': '7 d' };

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

const TILES: [string, keyof Pick<Stats, 'tasks_done' | 'moves' | 'handoffs' | 'blocks'>, IconName][] = [
  ['tasks done', 'tasks_done', 'done'],
  ['moves', 'moves', 'session'],
  ['handoffs', 'handoffs', 'reply'],
  ['blocks', 'blocks', 'blocked'],
];

const TILE =
  'stat-tile flex flex-col gap-1 rounded-card border border-transparent bg-surface px-4 py-3 shadow-[var(--shadow-card)] [&_svg]:text-ink-3 data-[warn]:[&_svg]:text-bad';

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
    <section className={CHART} aria-label="Agent time">
      <h3 className={CHART_TITLE}>Agent time</h3>
      {rows.length ? (
        rows.map((r) => (
          <div key={r.agent} className="bar-row my-1 grid grid-cols-[minmax(70px,40%)_1fr_auto] items-center gap-2" data-agent={r.agent}>
            <span className="bar-name truncate">{r.agent}</span>
            <span className="bar h-2 overflow-hidden rounded-chip bg-raised">
              <span className="bar-fill accent block h-full bg-ink-2" style={{ width: `${(r.seconds / top) * 100}%` }} />
            </span>
            <span className="bar-value font-mono text-label">{duration(r.seconds)}</span>
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
    </section>
  );
}

/** Account use lives on the Accounts page, one line per account; this is the way there. */
function AccountsLink() {
  return (
    <a className="stat-link flex h-11 items-center gap-2 rounded-card border border-line bg-surface px-4 text-ink no-underline hover:bg-raised" href="#/accounts" title="Account use: one line per account">
      <Icon name="accounts" size={16} />
      <span className="flex-1">Account use</span>
      <Icon name="chevronRight" size={14} label="Open" />
    </a>
  );
}

export function StatsPage() {
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
    <section className={`stats ${PAGE}`} aria-label="Stats">
      <header className={`stats-head ${PAGE_HEAD} [&_.ui-seg]:ml-auto`}>
        <h2 className={PAGE_TITLE}>Stats</h2>
        <Segmented label="Window" value={win} onChange={setWin} options={WINDOWS.map((w) => ({ value: w, label: WORD[w] }))} />
      </header>
      <View q={q} served={served} shape="stats" what="stats">
        {s ? (
          <>
            <p className="muted" data-span>
              {when(s.since)} to {when(s.until)}
            </p>
            <Tiles s={s} />
            <div className="charts mt-3 flex flex-col gap-3">
              <AgentTime s={s} />
              <AccountsLink />
            </div>
          </>
        ) : null}
      </View>
      {outlook ? <OutlookSection /> : null}
    </section>
  );
}
