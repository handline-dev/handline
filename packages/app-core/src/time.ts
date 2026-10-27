// The one place a time, an age or a length of time becomes text.

/** One unit, the biggest: "45s", "12m", "3h", "2d". */
export function shortSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`;
}

/** How long ago an ISO time was, as shortSpan; empty for none. */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  return iso ? shortSpan((now - Date.parse(iso)) / 1000) : '';
}

/** "2 h 10 m", "45 m", "3 d 4 h": the two biggest units that are not zero; "under a minute" below half a minute. */
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

const valid = (iso: string): Date | null => {
  const d = new Date(iso);
  return Number.isNaN(+d) ? null : d;
};

/** The time of day, "21:42" (24 hours); empty for a string that is not a date. */
export function clockTime(iso: string): string {
  return valid(iso)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }) ?? '';
}

/** Month, day and time, "Oct 15, 21:42"; empty for a string that is not a date. */
export function dateTime(iso: string): string {
  return valid(iso)?.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }) ?? '';
}
