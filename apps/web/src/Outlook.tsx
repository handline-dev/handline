// The cost per day (stats.outlook), stacked by agent, with a table of each agent's cost and share under it and a click on an agent to draw that
// one alone. A series has a shape (a pattern and a letter), not only a hue. The numbers are always in a table behind a toggle, so the picture
// is never the only way to read them. The week left is the Headroom tab's.
import { useState } from 'react';
import { costByAgent } from '@handline/app-core';
import type { Outlook } from '@handline/client';
import { useOutlook } from '@handline/app-data';
import { COST_BARS, COST_DAY, COST_LABEL, COST_PICK, COST_STACK, COST_SWATCH, DATA_TABLE, tone } from '#src/outlookStyle.ts';
import { Button, ChartCard, Segmented } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

type Range = Outlook['range'];
const COST: Range[] = ['7d', '14d', '30d'];

const letter = (i: number) => String.fromCharCode(65 + (i % 26));

function Cost({ o, only, pick }: { o: Outlook; only: string | null; pick: (agent: string | null) => void }) {
  const { agents, sums, all } = costByAgent(o);
  const shown = (d: Outlook['cost_per_day'][number], a: string) => (only === null || only === a ? (d.agents[a] ?? 0) : 0);
  const total = (d: Outlook['cost_per_day'][number]) => agents.reduce((sum, a) => sum + shown(d, a), 0);
  const top = Math.max(1, ...o.cost_per_day.map(total));
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
              <td>{Math.round((sums[i]! / (all || 1)) * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Table({ o }: { o: Outlook }) {
  return (
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
  const [range, setRange] = useState<Range>('7d');
  const [table, setTable] = useState(false);
  const [only, setOnly] = useState<string | null>(null);
  const q = useOutlook(range);
  const o = q.data;
  return (
    <ChartCard className="outlook mt-3" aria-label="Outlook">
      <div className="outlook-controls mb-2 flex flex-wrap items-center gap-2">
        <h3 className="m-0 flex-1 text-title font-semibold">Cost per day</h3>
        <Segmented label="Range" value={range} onChange={setRange} options={COST.map((r) => ({ value: r, label: r }))} />
        <Button icon="tasks" label="Table" variant="outline" aria-pressed={table} onClick={() => setTable(!table)} />
        {only ? <Button label="All agents" variant="outline" onClick={() => setOnly(null)} /> : null}
      </div>
      <View q={q} served shape="stats" what="the outlook">
        {o ? (
          <>
            <Cost o={o} only={only} pick={setOnly} />
            {table ? <Table o={o} /> : null}
          </>
        ) : null}
      </View>
    </ChartCard>
  );
}
