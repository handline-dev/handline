// The usage outlook (stats.outlook): the week left of the accounts, recorded and guessed, as a line; or the cost per day, stacked by agent, with
// a table of each agent's cost and share under it and a click on an agent to draw that one alone. A series has a shape (a dash, a pattern and a
// letter), not only a hue. The numbers are always in a table behind a toggle, so the picture is never the only way to read them.
import { useState } from 'react';
import { duration } from '@handline/app-core';
import type { Outlook } from '@handline/client';
import { useOutlook } from '@handline/app-data';
import {
  COST_BARS,
  COST_DAY,
  COST_LABEL,
  COST_PICK,
  COST_STACK,
  COST_SWATCH,
  DATA_TABLE,
  GRID_LINE,
  LEGEND,
  LEGEND_ITEM,
  LINE,
  NOW_LINE,
  OUTLOOK_END,
  tone,
} from './outlookStyle.ts';
import { Button, ChartCard, Select, Segmented } from './ui/index.ts';
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
  const lastLeft = all.at(-1)?.left_pct ?? null;
  const path = (pts: Outlook['past']) =>
    pts
      .filter((p) => p.left_pct !== null)
      .map((p, i) => `${i ? 'L' : 'M'}${x(p.at).toFixed(1)},${y(p.left_pct!).toFixed(1)}`)
      .join(' ');
  return (
    <>
      <div className="outlook-plot relative pr-[8.5rem]">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="outlook-svg h-[160px] w-full"
          role="img"
          aria-label={`Week left, the ${o.accounts} accounts with a reading together, recorded and guessed`}
        >
          {[0, 50, 100].map((g) => (
            <line key={g} x1={0} x2={W} y1={y(g)} y2={y(g)} className={GRID_LINE} />
          ))}
          <path d={path(o.past)} className={`${LINE} past`} fill="none" />
          <path d={path(o.future)} className={`${LINE} future`} fill="none" strokeDasharray="5 4" />
          <line x1={x(o.now)} x2={x(o.now)} y1={0} y2={H} className={NOW_LINE} />
        </svg>
        <span className={OUTLOOK_END} style={{ top: `${((100 - (lastLeft ?? 0)) / 100) * 100}%` }}>
          {o.accounts === 1 ? 'the account' : `all ${o.accounts} accounts`}
        </span>
      </div>
      <ul className={LEGEND} aria-label="Lines">
        <li className={LEGEND_ITEM}>
          <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden>
            <line x1="0" x2="24" y1="4" y2="4" className={`${LINE} past`} />
          </svg>{' '}
          recorded
        </li>
        <li className={LEGEND_ITEM}>
          <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden>
            <line x1="0" x2="24" y1="4" y2="4" className={`${LINE} future`} strokeDasharray="5 4" />
          </svg>{' '}
          guess
        </li>
        <li className={LEGEND_ITEM}>
          <svg viewBox="0 0 24 8" width="24" height="8" aria-hidden>
            <line x1="0" x2="24" y1="4" y2="4" className={NOW_LINE} />
          </svg>{' '}
          now
        </li>
      </ul>
    </>
  );
}

const letter = (i: number) => String.fromCharCode(65 + (i % 26));

function Cost({ o, only, pick }: { o: Outlook; only: string | null; pick: (agent: string | null) => void }) {
  const agents = [...new Set(o.cost_per_day.flatMap((d) => Object.keys(d.agents)))];
  const shown = (d: Outlook['cost_per_day'][number], a: string) => (only === null || only === a ? (d.agents[a] ?? 0) : 0);
  const total = (d: Outlook['cost_per_day'][number]) => agents.reduce((sum, a) => sum + shown(d, a), 0);
  const top = Math.max(1, ...o.cost_per_day.map(total));
  const sums = agents.map((a) => o.cost_per_day.reduce((sum, d) => sum + (d.agents[a] ?? 0), 0));
  const all = sums.reduce((a, b) => a + b, 0) || 1;
  return (
    <div className="cost-wrap">
      <div className={COST_BARS} role="img" aria-label={only ? `Cost per day, ${only} alone` : 'Cost per day, by agent'}>
        {o.cost_per_day.map((d) => (
          <div key={d.day} className={COST_DAY} data-day={d.day} title={`${d.day}: $${total(d).toFixed(2)}`}>
            <span className={COST_STACK} style={{ height: `${(total(d) / top) * 100}%` }}>
              {agents.map((a, i) => (
                <span key={a} className={`cost-seg basis-0 ${tone(i)}`} style={{ flexGrow: shown(d, a) }} />
              ))}
            </span>
            <span className={COST_LABEL}>{d.day.slice(5)}</span>
          </div>
        ))}
      </div>
      <table className={`${DATA_TABLE} agent-cost`} aria-label="Cost by agent">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Cost</th>
            <th>Share</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a, i) => (
            <tr key={a} data-agent={a} aria-selected={only === a} className="aria-selected:bg-raised">
              <td>
                <button
                  className={COST_PICK}
                  aria-pressed={only === a}
                  title={only === a ? 'Show every agent' : `Only ${a}`}
                  onClick={() => pick(only === a ? null : a)}
                >
                  <span className={`${COST_SWATCH} ${tone(i)}`} aria-hidden />
                  <b>{letter(i)}</b> {a}
                </button>
              </td>
              <td>${sums[i]!.toFixed(2)}</td>
              <td>{Math.round((sums[i]! / all) * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Table({ o, view }: { o: Outlook; view: 'left' | 'cost' }) {
  return view === 'left' ? (
    <table className={DATA_TABLE}>
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
    <table className={DATA_TABLE}>
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
  const [only, setOnly] = useState<string | null>(null);
  const ranges = view === 'left' ? HEADROOM : COST;
  const shown = ranges.includes(range) ? range : ranges[0]!;
  const q = useOutlook(shown);
  const o = q.data;
  return (
    <ChartCard className="outlook mt-3" aria-label="Outlook">
      <div className="outlook-controls mb-2 flex flex-wrap items-center gap-2">
        <h3 className="m-0 flex-1 text-title font-semibold">Outlook</h3>
        <Segmented
          label="View"
          value={view}
          onChange={setView}
          options={[
            { value: 'left', label: 'Session left' },
            { value: 'cost', label: 'Cost per day' },
          ]}
        />
        {ranges.length <= 3 ? (
          <Segmented label="Range" value={shown} onChange={setRange} options={ranges.map((r) => ({ value: r, label: r }))} />
        ) : (
          <Select
            label="Range"
            value={shown}
            onChange={(e) => setRange(e.target.value as Range)}
            options={ranges.map((r) => ({ value: r, label: r }))}
          />
        )}
        <Button icon="tasks" label="Table" variant="outline" aria-pressed={table} onClick={() => setTable(!table)} />
        {view === 'cost' && only ? <Button label="All agents" variant="outline" onClick={() => setOnly(null)} /> : null}
      </div>
      <View q={q} served shape="stats" what="the outlook">
        {o ? (
          <>
            {view === 'left' ? <Line o={o} /> : <Cost o={o} only={only} pick={setOnly} />}
            {view === 'left' ? (
              <ul className="runway mx-0 mt-2 mb-0 list-none p-0">
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
    </ChartCard>
  );
}
