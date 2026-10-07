// The Stats page (#/stats): the window's four counts, how long each agent worked, and the accounts' use, from stats.get; the usage outlook
// below them when the daemon serves it. The previous window's figures stay on screen until the new ones arrive.
import { useEffect, useState } from 'react';
import { agentTime, bandOf, duration } from '@handline/app-core';
import type { Stats } from '@handline/client';
import { useSelector } from '@handline/app-react';
import { useServes, useStats } from '@handline/app-data';
import { OutlookSection } from './Outlook.tsx';
import { View } from './View.tsx';

const WINDOWS = ['24h', '7d'] as const;
type Window = (typeof WINDOWS)[number];
const WORD: Record<Window, string> = { '24h': '24 h', '7d': '7 d' };

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

function Tiles({ s }: { s: Stats }) {
  const tiles: [string, number][] = [
    ['tasks done', s.tasks_done],
    ['moves', s.moves],
    ['handoffs', s.handoffs],
    ['blocks', s.blocks],
  ];
  return (
    <div className="stat-tiles">
      {tiles.map(([label, n]) => (
        <div key={label} className="stat-tile" data-tile={label} data-warn={label === 'blocks' && n > 0 ? '' : undefined}>
          <strong>{n}</strong>
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
    <section className="chart" aria-label="Agent time">
      <h3>Agent time</h3>
      {rows.length ? (
        rows.map((r) => (
          <div key={r.agent} className="bar-row" data-agent={r.agent}>
            <span className="bar-name">{r.agent}</span>
            <span className="bar">
              <span className="bar-fill accent" style={{ width: `${(r.seconds / top) * 100}%` }} />
            </span>
            <span className="bar-value">{duration(r.seconds)}</span>
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

function AccountUse({ s }: { s: Stats }) {
  const bar = (label: string, pct: number | null) =>
    pct === null ? (
      <div className="bar-row" data-use={label}>
        <span className="bar-name">{label}</span>
        <span className="muted">no data</span>
      </div>
    ) : (
      <div className="bar-row" data-use={label} data-band={bandOf(pct)}>
        <span className="bar-name">{label}</span>
        <span className="bar">
          <span className={`bar-fill ${bandOf(pct)}`} style={{ width: `${Math.min(100, pct)}%` }} />
        </span>
        <span className="bar-value">{Math.round(pct)}%</span>
      </div>
    );
  return (
    <section className="chart" aria-label="Account use">
      <h3>Account use</h3>
      {s.per_account.length ? (
        s.per_account.map((a) => (
          <div key={a.account} className="use-account" data-account={a.account}>
            <strong>{a.account}</strong>
            {bar('5 h', a.five_hour_used_pct)}
            {bar('week', a.weekly_used_pct)}
          </div>
        ))
      ) : (
        <p className="muted">Nothing recorded in this window.</p>
      )}
    </section>
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
    <section className="stats" aria-label="Stats">
      <header className="inbox-top">
        <h2>Stats</h2>
        <div role="group" className="segmented" aria-label="Window">
          {WINDOWS.map((w) => (
            <button key={w} className={`chat-tab${win === w ? ' on' : ''}`} aria-pressed={win === w} onClick={() => setWin(w)}>
              {WORD[w]}
            </button>
          ))}
        </div>
      </header>
      <View q={q} served={served} shape="stats" what="stats">
        {s ? (
          <>
            <p className="muted" data-span>
              {when(s.since)} to {when(s.until)}
            </p>
            <Tiles s={s} />
            <div className="charts">
              <AgentTime s={s} />
              <AccountUse s={s} />
            </div>
          </>
        ) : null}
      </View>
      {outlook ? <OutlookSection /> : null}
    </section>
  );
}
