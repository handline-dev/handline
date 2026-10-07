// What an account's usage windows say, for the gauge cards and the usage popup: the bar's colour, how far through the window we are,
// the reset in words, and how long the rest lasts at the pace so far. Pure: the page passes the time.
import type { Account, Window } from '@handline/client';

export const FIVE_HOURS_MS = 5 * 3_600_000;
export const WEEK_MS = 7 * 86_400_000;

/** The bridge's gauge thresholds (`gauge.py`): from 60% the bar warns, from 85% it is failing. */
export type Tone = 'ok' | 'warn' | 'bad';
export const toneOf = (pct: number): Tone => (pct >= 85 ? 'bad' : pct >= 60 ? 'warn' : 'ok');

export interface Gauge {
  pct: number;
  tone: Tone;
  /** How far through the window the clock is, 0 to 100, or null when the reset is unknown. */
  through: number | null;
  /** Milliseconds to the reset, or null. */
  resetIn: number | null;
  /** Milliseconds until the window is full at the pace so far; null with no pace to go on or when the reset comes first. */
  runway: number | null;
  /** The number's source ("measured", "estimated"). */
  source: string;
}

export function gaugeOf(w: Window, spanMs: number, now: number): Gauge {
  const pct = Math.min(100, Math.max(0, Math.round(w.used_pct)));
  const resetIn = w.resets_at ? Math.max(0, Date.parse(w.resets_at) - now) : null;
  const elapsed = resetIn === null ? null : Math.min(spanMs, Math.max(0, spanMs - resetIn));
  const through = elapsed === null ? null : Math.round((elapsed / spanMs) * 100);
  let runway: number | null = null;
  if (elapsed && pct > 0) {
    const left = ((100 - pct) / pct) * elapsed;
    runway = resetIn !== null && left >= resetIn ? null : left;
  }
  return { pct, tone: toneOf(pct), through, resetIn, runway, source: w.source };
}

/** "2 h 10 m", "45 m", "3 d 4 h": the two biggest units that are not zero. */
export function span(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 1) return 'under a minute';
  const d = Math.floor(m / 1440);
  const h = Math.floor((m % 1440) / 60);
  const min = m % 60;
  if (d) return h ? `${d} d ${h} h` : `${d} d`;
  if (h) return min ? `${h} h ${min} m` : `${h} h`;
  return `${min} m`;
}

export type AccountState = 'needs_login' | 'out' | 'in_use' | 'idle' | 'unknown';
export const STATE_WORD: Record<AccountState, string> = {
  needs_login: 'login needed',
  out: 'out',
  in_use: 'in use',
  idle: 'idle',
  unknown: 'unknown',
};

/** The state chip: login first, then a full window (out until it resets), then whether agents sit on it. */
export function stateOf(a: Account): AccountState {
  if (a.login_state === 'needs_login') return 'needs_login';
  if ((a.five_hour?.used_pct ?? 0) >= 100 || (a.weekly?.used_pct ?? 0) >= 100) return 'out';
  if (a.login_state === 'unknown') return 'unknown';
  return a.agents.length ? 'in_use' : 'idle';
}

/** When an account that is out comes back: the latest reset among its full windows. */
export function outUntil(a: Account): string | null {
  const full = [a.five_hour, a.weekly].filter((w): w is Window => !!w && w.used_pct >= 100 && !!w.resets_at);
  return (
    full
      .map((w) => w.resets_at!)
      .sort()
      .at(-1) ?? null
  );
}
