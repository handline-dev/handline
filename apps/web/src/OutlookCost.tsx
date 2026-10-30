// The cost per day of the usage outlook (stats.outlook): a bar per day stacked by agent, and under it each agent's cost and share, a click on an
// agent drawing that one alone. A series has a pattern and a letter, not only a tone.
import { useState } from 'react';
import { costByAgent } from '@handline/app-core';
import type { Outlook } from '@handline/client';

const TONES = [
  'bg-ink-2',
  '[background:repeating-linear-gradient(45deg,var(--ink-2)_0_2px,var(--bg-1)_2px_4px)]',
  '[background:radial-gradient(var(--ink-2)_30%,var(--bg-1)_35%)_0_0/4px_4px]',
  '[background:repeating-linear-gradient(0deg,var(--ink-2)_0_1px,transparent_1px_4px),repeating-linear-gradient(90deg,var(--ink-2)_0_1px,var(--bg-1)_1px_4px)]',
] as const;
const tone = (i: number) => `c${i % 4} ${TONES[i % 4]}`;
const letter = (i: number) => String.fromCharCode(65 + (i % 26));

export function Cost({ o }: { o: Outlook }) {
  const [only, setOnly] = useState<string | null>(null);
  const { agents, sums, all } = costByAgent(o);
  const shown = (d: Outlook['cost_per_day'][number], a: string) => (only === null || only === a ? (d.agents[a] ?? 0) : 0);
  const total = (d: Outlook['cost_per_day'][number]) => agents.reduce((sum, a) => sum + shown(d, a), 0);
  const top = Math.max(1, ...o.cost_per_day.map(total));
  return (
    <div className="cost-wrap mt-4">
      <div
        className="cost flex h-[200px] items-end gap-1"
        role="img"
        aria-label={only ? `Cost per day, ${only} alone` : 'Cost per day, by agent'}
      >
        {o.cost_per_day.map((d) => (
          <div
            key={d.day}
            className="cost-day flex h-full min-w-0 flex-1 flex-col justify-end"
            data-day={d.day}
            title={`${d.day}: $${total(d).toFixed(2)}`}
          >
            <span className="cost-stack flex min-h-0.5 flex-col-reverse" style={{ height: `${(total(d) / top) * 100}%` }}>
              {agents.map((a, i) => (
                <span key={a} className={`cost-seg basis-0 ${tone(i)}`} style={{ flexGrow: shown(d, a) }} />
              ))}
            </span>
            <span className="muted overflow-hidden text-center text-sm">{d.day.slice(5)}</span>
          </div>
        ))}
      </div>
      <table className="agent-cost mt-3 w-full border-collapse text-sm" aria-label="Cost by agent">
        <thead>
          <tr className="muted text-left text-xs">
            <th className="py-1 font-normal">Agent</th>
            <th className="py-1 font-normal">Cost</th>
            <th className="py-1 font-normal">Share</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a, i) => (
            <tr key={a} data-agent={a} aria-selected={only === a} className="aria-selected:bg-raised">
              <td className="py-1">
                <button
                  className="cost-pick inline-flex items-center gap-1.5 border-0 bg-transparent p-0 text-left aria-pressed:font-semibold aria-pressed:underline"
                  aria-pressed={only === a}
                  title={only === a ? 'Show every agent' : `Only ${a}`}
                  onClick={() => setOnly(only === a ? null : a)}
                >
                  <span
                    className={`cost-swatch inline-block size-3.5 rounded-sm border border-line-strong align-middle ${tone(i)}`}
                    aria-hidden
                  />
                  <b>{letter(i)}</b> {a}
                </button>
              </td>
              <td className="py-1">${sums[i]!.toFixed(2)}</td>
              <td className="py-1">{Math.round((sums[i]! / (all || 1)) * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
