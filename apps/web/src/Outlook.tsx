// The usage outlook (stats.outlook): the week left of the accounts, recorded and guessed, as a line; or the cost per day, stacked by agent.
// The numbers are always in a table behind a toggle, so the picture is never the only way to read them.
import { useState } from 'react';
import { duration } from '@handline/app-core';
import type { Outlook } from '@handline/client';
import { useOutlook } from '@handline/app-data';
import { View } from './View.tsx';

type Range = Outlook['range'];
const HEADROOM: Range[] = ['12h', '24h', '3d', '7d', '14d'];
const COST: Range[] = ['7d', '14d', '30d'];
const W = 560;
const H = 160;

const runwayWords = (r: Outlook['runway'][number]) =>
  r.runs_out_in_seconds !== null
    ? `runs out in ${duration(r.runs_out_in_seconds)}`
    : r.week_resets_at
      ? 'lasts until the week resets'
      : 'no burn, or a stale reading';

function Line({ o }: { o: Outlook }) {
  const all = [...o.past, ...o.future].filter((p) => p.left_pct !== null);
  const t0 = Date.parse((all[0] ?? { at: o.now }).at);
  const t1 = Date.parse((all.at(-1) ?? { at: o.now }).at);
  const x = (at: string) => (t1 === t0 ? 0 : ((Date.parse(at) - t0) / (t1 - t0)) * W);
  const y = (pct: number) => H - (pct / 100) * H;
  const path = (pts: Outlook['past']) =>
    pts
      .filter((p) => p.left_pct !== null)
      .map((p, i) => `${i ? 'L' : 'M'}${x(p.at).toFixed(1)},${y(p.left_pct!).toFixed(1)}`)
      .join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="outlook-svg" role="img" aria-label="Week left across the accounts, recorded and guessed">
      {[0, 50, 100].map((g) => (
        <line key={g} x1={0} x2={W} y1={y(g)} y2={y(g)} className="grid" />
      ))}
      <path d={path(o.past)} className="line past" fill="none" />
      <path d={path(o.future)} className="line future" fill="none" strokeDasharray="5 4" />
      <line x1={x(o.now)} x2={x(o.now)} y1={0} y2={H} className="now" />
      <text x={x(o.now) + 4} y={12} className="svg-label">
        now
      </text>
      <text x={W - 4} y={12} textAnchor="end" className="svg-label">
        guess →
      </text>
    </svg>
  );
}

function Cost({ o }: { o: Outlook }) {
  const agents = [...new Set(o.cost_per_day.flatMap((d) => Object.keys(d.agents)))];
  const total = (d: Outlook['cost_per_day'][number]) => Object.values(d.agents).reduce((a, b) => a + b, 0);
  const top = Math.max(1, ...o.cost_per_day.map(total));
  return (
    <div className="cost-wrap">
      <div className="cost" role="img" aria-label="Cost per day, by agent">
        {o.cost_per_day.map((d) => (
          <div key={d.day} className="cost-day" data-day={d.day} title={`${d.day}: $${total(d).toFixed(2)}`}>
            <span className="cost-stack" style={{ height: `${(total(d) / top) * 100}%` }}>
              {agents.map((a, i) => (
                <span key={a} className={`cost-seg c${i % 4}`} style={{ flexGrow: d.agents[a] ?? 0 }} />
              ))}
            </span>
            <span className="cost-label muted">{d.day.slice(5)}</span>
          </div>
        ))}
      </div>
      <ul className="cost-legend muted" aria-label="Agents">
        {agents.map((a, i) => (
          <li key={a}>
            <span className={`cost-swatch c${i % 4}`} /> {a}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Table({ o, view }: { o: Outlook; view: 'left' | 'cost' }) {
  return view === 'left' ? (
    <table className="data-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Week left</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {[...o.past.map((p) => ({ ...p, guess: false })), ...o.future.map((p) => ({ ...p, guess: true }))].map((p) => (
          <tr key={p.at}>
            <td>{new Date(p.at).toLocaleString()}</td>
            <td>{p.left_pct === null ? 'no data' : `${Math.round(p.left_pct)}%`}</td>
            <td className="muted">{p.guess ? 'guess' : ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <table className="data-table">
      <thead>
        <tr>
          <th>Day</th>
          <th>Agent</th>
          <th>Cost</th>
        </tr>
      </thead>
      <tbody>
        {o.cost_per_day.flatMap((d) =>
          Object.entries(d.agents).map(([a, c]) => (
            <tr key={`${d.day}${a}`}>
              <td>{d.day}</td>
              <td>{a}</td>
              <td>${c.toFixed(2)}</td>
            </tr>
          )),
        )}
      </tbody>
    </table>
  );
}

export function OutlookSection() {
  const [view, setView] = useState<'left' | 'cost'>('left');
  const [range, setRange] = useState<Range>('24h');
  const [table, setTable] = useState(false);
  const ranges = view === 'left' ? HEADROOM : COST;
  const shown = ranges.includes(range) ? range : ranges[0]!;
  const q = useOutlook(shown);
  const o = q.data;
  return (
    <section className="chart outlook" aria-label="Outlook">
      <h3>Outlook</h3>
      <div className="outlook-controls">
        <select aria-label="View" value={view} onChange={(e) => setView(e.target.value as 'left' | 'cost')}>
          <option value="left">Session left</option>
          <option value="cost">Cost per day</option>
        </select>
        <select aria-label="Range" value={shown} onChange={(e) => setRange(e.target.value as Range)}>
          {ranges.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <button className="btn" aria-pressed={table} onClick={() => setTable(!table)}>
          Table
        </button>
      </div>
      <View q={q} served shape="stats" what="the outlook">
        {o ? (
          <>
            {view === 'left' ? <Line o={o} /> : <Cost o={o} />}
            {view === 'left' ? (
              <ul className="runway">
                {o.runway.map((r) => (
                  <li key={r.account} data-runway={r.account}>
                    <strong>{r.account}</strong> <span className="muted">{runwayWords(r)}</span>
                  </li>
                ))}
              </ul>
            ) : null}
            {table ? <Table o={o} view={view} /> : null}
          </>
        ) : null}
      </View>
    </section>
  );
}
