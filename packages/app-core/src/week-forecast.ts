// The week across all accounts in two facts for the status bar: how much is left and whether it lasts to the reset at the current pace.
// The pace is the Outlook's guess line, so the page, the bar and the phone agree. Pure.
import type { Account, Outlook } from '@handline/client';
import { span } from '#src/time.ts';

export type WeekForecast = {
  /** Percent of the week left, averaged over the accounts that have a reading; null with none. */
  leftPct: number | null;
  kind: 'lasts' | 'runs_out' | 'unknown';
  /** When the guess line reaches nothing left; only for `runs_out`. */
  runsOutAt: number | null;
  /** Milliseconds from now to that, and to the soonest week reset; each null when unknown. */
  runsOutInMs: number | null;
  resetInMs: number | null;
};

const WALL = 0.5;

export function weekForecast(accounts: readonly Account[], outlook: Pick<Outlook, 'future'> | undefined, now: number): WeekForecast {
  const left = accounts.flatMap((a) => (a.weekly ? [100 - a.weekly.used_pct] : []));
  const leftPct = left.length ? Math.max(0, Math.round(left.reduce((n, x) => n + x, 0) / left.length)) : null;
  const resets = accounts.flatMap((a) => (a.weekly?.resets_at ? [Date.parse(a.weekly.resets_at) - now] : [])).filter((ms) => ms > 0);
  const resetInMs = resets.length ? Math.min(...resets) : null;
  const guess = outlook?.future.filter((p) => p.left_pct !== null) ?? [];
  const wall = guess.find((p) => p.left_pct! <= WALL);
  if (leftPct === null || !guess.length) return { leftPct, kind: 'unknown', runsOutAt: null, runsOutInMs: null, resetInMs };
  if (!wall) return { leftPct, kind: 'lasts', runsOutAt: null, runsOutInMs: null, resetInMs };
  const at = Date.parse(wall.at);
  return { leftPct, kind: 'runs_out', runsOutAt: at, runsOutInMs: Math.max(0, at - now), resetInMs };
}

/** "lasts until the week resets", or "runs out Thu 05:00, 9 h before it resets"; nothing for an unknown pace. */
export function forecastWords(f: WeekForecast, day: (ms: number) => string): string {
  if (f.kind === 'lasts') return 'lasts until the week resets';
  if (f.kind === 'unknown' || f.runsOutAt === null) return 'pace not known yet';
  const before =
    f.resetInMs !== null && f.runsOutInMs !== null && f.resetInMs > f.runsOutInMs
      ? `, ${span(f.resetInMs - f.runsOutInMs)} before it resets`
      : '';
  return `runs out ${day(f.runsOutAt)}${before}`;
}

/** Whether the bar warns: the week runs out in under `hours` (0 is off). */
export const runsOutSoon = (f: WeekForecast, hours: number): boolean =>
  hours > 0 && f.kind === 'runs_out' && f.runsOutInMs! < hours * 3_600_000;

/** The notice text when it does. */
export function alertText(f: WeekForecast): string {
  const reset = f.resetInMs !== null ? ` (before it resets in ${span(f.resetInMs)})` : '';
  return `At this pace the week runs out in about ${span(f.runsOutInMs ?? 0)}${reset}`;
}
