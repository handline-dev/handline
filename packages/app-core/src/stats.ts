// The numbers the Stats and Home settings pages show and the text they accept: bar colours, durations, and the two fields that change units
// or may be cleared. Pure.
import type { PageSettings } from '#src/shell.ts';

/** The bridge's gauge bands (`gauge.py`): from 60% a bar warns, from 85% it fails. */
export type Band = 'ok' | 'warn' | 'bad';
export const bandOf = (pct: number): Band => (pct >= 85 ? 'bad' : pct >= 60 ? 'warn' : 'ok');

/** Agents by time worked, longest first, and how many had none (folded into "n idle agents"). */
export function agentTime(working: Record<string, number>): { rows: { agent: string; seconds: number }[]; idle: number } {
  const all = Object.entries(working).map(([agent, seconds]) => ({ agent, seconds }));
  return { rows: all.filter((r) => r.seconds > 0).sort((a, b) => b.seconds - a.seconds), idle: all.filter((r) => r.seconds <= 0).length };
}

export const INVALID = Symbol('invalid');

/** The default cap field: empty clears it (null, back to the default), a whole number from 1 is sent, anything else is not. */
export function parseCap(text: string): number | null | typeof INVALID {
  const t = text.trim();
  if (t === '') return null;
  return /^\d+$/.test(t) && Number(t) >= 1 ? Number(t) : INVALID;
}

/** The idle close field is whole minutes, stored as seconds; 0 is never, empty clears it. */
export function parseMinutes(text: string): number | null | typeof INVALID {
  const t = text.trim();
  if (t === '') return null;
  return /^\d+$/.test(t) ? Number(t) * 60 : INVALID;
}
/** A seconds field: empty clears it (null), whole seconds are sent, anything else is not. */
export function parseSeconds(text: string): number | null | typeof INVALID {
  const t = text.trim();
  if (t === '') return null;
  return /^\d+$/.test(t) ? Number(t) : INVALID;
}
/** A machine limit: a whole number, `off` or 0 for no limit, empty for the default. */
export function parseLimit(text: string): number | null | typeof INVALID {
  const t = text.trim().toLowerCase();
  if (t === '') return null;
  if (t === 'off') return 0;
  return /^\d+$/.test(t) ? Number(t) : INVALID;
}
const limitOf = (n: number | undefined): string => (n === undefined ? '' : n === 0 ? 'off' : String(n));
export const minutesOf = (seconds: number | undefined): string => (seconds === undefined ? '' : String(Math.round(seconds / 60)));

/** The default cap field of the Settings page, on the PC and the phone: everything but the words around it and the layout. */
export const capField = (activeCap: number | undefined) => ({
  label: 'Default cap',
  unit: 'agents',
  current: activeCap === undefined ? '' : String(activeCap),
  parse: parseCap,
  change: (n: number | null): PageSettings => ({ active_cap: n }),
  allowed: 'a whole number from 1, or empty for the default',
});

/** The idle close field, likewise. */
export const idleField = (parkTimeoutS: number | undefined) => ({
  label: 'Idle close',
  unit: 'min',
  current: minutesOf(parkTimeoutS),
  parse: parseMinutes,
  change: (s: number | null): PageSettings => ({ park_timeout_s: s }),
  allowed: 'whole minutes, 0 for never, or empty for the default',
});

/** The machine load limits of the Settings page, on the PC and the phone: the daemon starts no new work over any of them; 0 is off. */
export const limitFields = (v: { host_active_cap?: number; load_per_core_pct?: number; ram_free_min_pct?: number }) => {
  const allowed = 'a whole number, 0 or off for no limit, or empty for the default';
  const field = (
    label: string,
    hint: string,
    unit: string,
    n: number | undefined,
    key: 'host_active_cap' | 'load_per_core_pct' | 'ram_free_min_pct',
    change: (x: number | null) => PageSettings,
  ) => ({
    setting: key,
    label,
    hint,
    unit,
    current: limitOf(n),
    parse: parseLimit,
    change,
    allowed,
  });
  // A daemon that has none of the keys does not know the limits: no rows.
  if (v.host_active_cap === undefined && v.load_per_core_pct === undefined && v.ram_free_min_pct === undefined) return [];
  return [
    field(
      'Agents at once',
      'Agents working at once on this machine, over every account',
      'agents',
      v.host_active_cap,
      'host_active_cap',
      (x) => ({ host_active_cap: x }),
    ),
    field(
      'Load per core',
      'Most 1-minute load per core; over it no new work starts (150 is 1.5)',
      '%',
      v.load_per_core_pct,
      'load_per_core_pct',
      (x) => ({ load_per_core_pct: x }),
    ),
    field('Least free memory', 'Under this share of memory free, no new work starts', '%', v.ram_free_min_pct, 'ram_free_min_pct', (x) => ({
      ram_free_min_pct: x,
    })),
  ];
};

/** A whole number from `min` to `max`; empty clears it (null, back to the default), anything out of range is not sent. */
export const parseRange =
  (min: number, max: number) =>
  (text: string): number | null | typeof INVALID => {
    const t = text.trim();
    if (t === '') return null;
    return /^\d+$/.test(t) && Number(t) >= min && Number(t) <= max ? Number(t) : INVALID;
  };

/** The pacing rows of the Settings page, on the PC and the phone: the target the week is paced to and the burst allowed over the line. A daemon that has none of the keys does not pace: no rows. */
export const paceFields = (v: { pacing?: boolean; pace_target_pct?: number; pace_burst_pct?: number }) => {
  if (v.pacing === undefined && v.pace_target_pct === undefined && v.pace_burst_pct === undefined) return [];
  const field = (
    label: string,
    hint: string,
    n: number | undefined,
    min: number,
    max: number,
    key: 'pace_target_pct' | 'pace_burst_pct',
  ) => ({
    label,
    hint,
    unit: '%',
    current: n === undefined ? '' : String(n),
    parse: parseRange(min, max),
    change: (x: number | null): PageSettings => ({ [key]: x }),
    allowed: `a whole number from ${min} to ${max}, or empty for the default`,
  });
  return [
    field('Pace target', "The share of its week an account may have used by the week's end", v.pace_target_pct, 50, 100, 'pace_target_pct'),
    field('Pace burst', 'Points over the pace line before an account starts no new turn', v.pace_burst_pct, 0, 50, 'pace_burst_pct'),
  ];
};
