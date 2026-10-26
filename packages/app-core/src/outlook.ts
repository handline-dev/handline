// What the Outlook page says in words and sums, the same on every app. Pure.
import type { Outlook } from '@handline/client';
import { span } from '#src/time.ts';

/** How long an account's week lasts at the pace so far. */
export const runwayWords = (r: Outlook['runway'][number]): string =>
  r.runs_out_in_seconds !== null
    ? `runs out in ${span(r.runs_out_in_seconds * 1000)}`
    : r.week_resets_at
      ? 'lasts until the week resets'
      : 'no burn, or a stale reading';

/** The agents with a cost in the window, each one's total in the same order, and the grand total. */
export function costByAgent(o: Outlook): { agents: string[]; sums: number[]; all: number } {
  const agents = [...new Set(o.cost_per_day.flatMap((d) => Object.keys(d.agents)))];
  const sums = agents.map((a) => o.cost_per_day.reduce((sum, d) => sum + (d.agents[a] ?? 0), 0));
  return { agents, sums, all: sums.reduce((a, b) => a + b, 0) };
}

/** The points of the guess line: it starts at the last recorded point, so the two lines join, then goes on with the guessed ones after it. */
export function guessLine(o: Pick<Outlook, 'past' | 'future'>): Outlook['future'] {
  const last = o.past.filter((p) => p.left_pct !== null).at(-1);
  return last ? [last, ...o.future.filter((p) => Date.parse(p.at) > Date.parse(last.at))] : o.future;
}
