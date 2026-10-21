// What the headroom graph says in words and finds under a finger, the same on every app. Pure.
import type { Headroom } from '@handline/client';
import { dateTime, span } from '#src/time.ts';

export type HeadroomPoint = { at: string; left_pct: number | null };

/** Combined points, recorded then guessed. */
export const headroomPoints = (h: Headroom): HeadroomPoint[] => [...h.combined.past, ...h.combined.future];

/** The reading nearest to a time (ms), skipping points with no data; null when there is none. */
export function nearestPoint<T extends { at: string }>(points: T[], t: number, has: (p: T) => boolean = () => true): T | null {
  let best: T | null = null;
  let gap = Infinity;
  for (const p of points) {
    if (!has(p)) continue;
    const d = Math.abs(Date.parse(p.at) - t);
    if (d < gap) [best, gap] = [p, d];
  }
  return best;
}

/** The one line over the graph: what is left now, when it runs out, the next reset. */
export function headroomSummary(h: Headroom): string {
  const now = Date.parse(h.now);
  const last = [...h.combined.past].reverse().find((p) => p.left_pct !== null);
  const left = last ? `${Math.round(last.left_pct!)}% left` : 'no reading yet';
  const floor = h.runs_out_at ? `runs out ${dateTime(h.runs_out_at)}` : 'lasts through the span';
  const reset = h.next_reset
    ? `next reset in ${span(Date.parse(h.next_reset.at) - now)} (${h.next_reset.account} ${h.next_reset.window})`
    : 'no reset ahead';
  return [left, floor, reset].join(' · ');
}

/** The balloon over a dragged line: the time and the percent there. */
export const headroomStamp = (at: string, pct: number | null): string =>
  `${dateTime(at)} · ${pct === null ? 'no data' : `${Math.round(pct)}%`}`;
