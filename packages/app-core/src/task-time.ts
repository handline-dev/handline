// Where a task's time went as the screen draws it: task.time's segments in order, and the seconds each kind took.
import type { TimeSegment } from '@handline/client';

export type TimeKind = TimeSegment['kind'];

/** The kinds in the order the totals list them. */
export const TIME_KINDS: readonly TimeKind[] = ['working', 'waiting', 'queued', 'starting'];

const WORD: Record<TimeKind, string> = { working: 'Working', waiting: 'Waiting', queued: 'Up next', starting: 'Starting' };

export interface TimeRow {
  kind: TimeKind;
  /** The reason as the daemon words it; `working` for a stretch with none. */
  text: string;
  from: string;
  to: string;
  seconds: number;
  /** The share of the whole, 0 to 1. */
  share: number;
}
export interface TimeTotal {
  kind: TimeKind;
  word: string;
  seconds: number;
}
export interface TimeView {
  rows: TimeRow[];
  /** Only the kinds the task spent time in. */
  totals: TimeTotal[];
  /** The sum of the segments. */
  seconds: number;
}

/** The segments in order of time, with each one's share of the whole and the total per kind. */
export function timeView(segments: TimeSegment[]): TimeView {
  const sorted = [...segments].sort((a, b) => a.from.localeCompare(b.from));
  const seconds = sorted.reduce((n, s) => n + s.seconds, 0);
  const rows = sorted.map((s) => ({
    kind: s.kind,
    text: s.text || WORD[s.kind].toLowerCase(),
    from: s.from,
    to: s.to,
    seconds: s.seconds,
    share: seconds ? s.seconds / seconds : 0,
  }));
  const totals = TIME_KINDS.map((kind) => ({
    kind,
    word: WORD[kind],
    seconds: sorted.filter((s) => s.kind === kind).reduce((n, s) => n + s.seconds, 0),
  })).filter((t) => t.seconds > 0);
  return { rows, totals, seconds };
}
