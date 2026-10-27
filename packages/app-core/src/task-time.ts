// Where a task's time went as the screen draws it: task.time's segments in order, and the seconds each kind took.
import type { TimeSegment } from '@handline/client';

export type TimeKind = TimeSegment['kind'];

/** What a stretch is, in the person's words: a waiting stretch is told by what it waits on. */
export type TimeLabel = 'working' | 'founder' | 'task' | 'agent' | 'account' | 'waiting' | 'queued' | 'starting';

/** The labels in the order the totals list them. */
export const TIME_LABELS: readonly TimeLabel[] = ['working', 'founder', 'task', 'agent', 'account', 'waiting', 'queued', 'starting'];

const WORD: Record<TimeLabel, string> = {
  working: 'Working',
  founder: 'Needs you',
  task: 'Blocked',
  agent: 'Waiting on an agent',
  account: 'At a usage limit',
  waiting: 'Waiting',
  queued: 'Waiting to start',
  starting: 'Starting',
};

export const labelOf = (s: Pick<TimeSegment, 'kind' | 'on'>): TimeLabel => (s.kind === 'waiting' ? (s.on ?? 'waiting') : s.kind);

export interface TimeRow {
  kind: TimeKind;
  label: TimeLabel;
  /** The reason as the daemon words it; `working` for a stretch with none. */
  text: string;
  from: string;
  to: string;
  seconds: number;
  /** The share of the whole, 0 to 1. */
  share: number;
}
export interface TimeTotal {
  label: TimeLabel;
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
    label: labelOf(s),
    text: !s.text || s.text === 'queued' ? WORD[labelOf(s)].toLowerCase() : s.text,
    from: s.from,
    to: s.to,
    seconds: s.seconds,
    share: seconds ? s.seconds / seconds : 0,
  }));
  const totals = TIME_LABELS.map((label) => ({
    label,
    word: WORD[label],
    seconds: sorted.filter((s) => labelOf(s) === label).reduce((n, s) => n + s.seconds, 0),
  })).filter((t) => t.seconds > 0);
  return { rows, totals, seconds };
}
