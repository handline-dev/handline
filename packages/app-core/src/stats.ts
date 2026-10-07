// The numbers the Stats and Home settings pages show and the text they accept: bar colours, durations, and the two fields that change units
// or may be cleared. Pure.

/** The bridge's gauge bands (`gauge.py`): from 60% a bar warns, from 85% it fails. */
export type Band = 'ok' | 'warn' | 'bad';
export const bandOf = (pct: number): Band => (pct >= 85 ? 'bad' : pct >= 60 ? 'warn' : 'ok');

/** "3 h 12 m", "45 m", "50 s" for under a minute, "0 m" for none. */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s ? `${s} s` : '0 m';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} m` : `${m} m`;
}

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
export const minutesOf = (seconds: number | undefined): string => (seconds === undefined ? '' : String(Math.round(seconds / 60)));
