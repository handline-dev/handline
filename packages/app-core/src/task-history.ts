// The change history of a task as the screen draws it: task.history rows filtered, grouped by edit, folded and cut into days.
import type { HistoryRow } from '@handline/client';

export type HistoryFilter = 'all' | 'fields' | 'state' | 'memo';

/** The rows one person changed at one time: an edit of several fields is several rows with the same at. */
export interface HistoryEdit {
  kind: 'edit';
  at: string;
  by: string;
  rows: HistoryRow[];
}
/** Memo changes that sit together, folded to one line under All. */
export interface HistoryFold {
  kind: 'memos';
  rows: HistoryRow[];
  by: string[];
  /** The oldest and newest times. */
  from: string;
  to: string;
}
export type HistoryItem = HistoryEdit | HistoryFold;
export interface HistoryDay {
  /** The local date, YYYY-MM-DD. */
  day: string;
  items: HistoryItem[];
}

const isMemo = (r: HistoryRow) => r.field === 'memo';
const isState = (r: HistoryRow) => r.field === 'state';

const KEEP: Record<HistoryFilter, (r: HistoryRow) => boolean> = {
  all: () => true,
  fields: (r) => !isMemo(r) && !isState(r),
  state: isState,
  memo: isMemo,
};

/** How many rows each chip holds. */
export function historyCounts(rows: HistoryRow[]): Record<HistoryFilter, number> {
  return {
    all: rows.length,
    fields: rows.filter(KEEP.fields).length,
    state: rows.filter(KEEP.state).length,
    memo: rows.filter(KEEP.memo).length,
  };
}

/** The local date of an ISO time. */
export function dayOf(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** What a memo row did, from its before and after. */
export function memoAction(r: HistoryRow): 'added' | 'edited' | 'deleted' {
  return !r.was ? 'added' : !r.now ? 'deleted' : 'edited';
}

/** The first line of a text, and whether more follows it (another line, or a line too long to show whole). */
export function firstLine(text: string, max = 80): { head: string; more: boolean } {
  const line = text.split('\n', 1)[0]!;
  const cut = line.length > max;
  return { head: cut ? `${line.slice(0, max).trimEnd()}…` : line, more: cut || line.length < text.length };
}

/**
 * The days of the history, each with its items in the order asked for. A memo row stays its own item when the filter is Memo; under All a day's
 * memo rows fold to one item, where the newest of them sits. Rows of one person at one time sit in one edit.
 */
export function historyView(rows: HistoryRow[], filter: HistoryFilter, newestFirst = true): HistoryDay[] {
  const sorted = rows
    .filter(KEEP[filter])
    .map((r, i) => [r, i] as const)
    .sort(([a, i], [b, j]) =>
      a.at === b.at ? (newestFirst ? i - j : j - i) : newestFirst ? b.at.localeCompare(a.at) : a.at.localeCompare(b.at),
    )
    .map(([r]) => r);
  const days: HistoryDay[] = [];
  for (const r of sorted) {
    const day = dayOf(r.at);
    const into = days[days.length - 1]?.day === day ? days[days.length - 1]! : days[days.push({ day, items: [] }) - 1]!;
    const last = into.items[into.items.length - 1];
    if (filter === 'all' && isMemo(r)) {
      const fold = into.items.find((i): i is HistoryFold => i.kind === 'memos');
      if (fold) {
        fold.rows.push(r);
        if (!fold.by.includes(r.by)) fold.by.push(r.by);
        fold.from = fold.from < r.at ? fold.from : r.at;
        fold.to = fold.to > r.at ? fold.to : r.at;
      } else into.items.push({ kind: 'memos', rows: [r], by: [r.by], from: r.at, to: r.at });
    } else if (last?.kind === 'edit' && last.at === r.at && last.by === r.by && !isMemo(r)) last.rows.push(r);
    else into.items.push({ kind: 'edit', at: r.at, by: r.by, rows: [r] });
  }
  return days;
}

/** The day divider's words: Today, Yesterday, else the date. */
export function dayLabel(day: string, now = Date.now()): string {
  const today = dayOf(new Date(now).toISOString());
  const yesterday = dayOf(new Date(now - 86_400_000).toISOString());
  if (day === today) return 'Today';
  if (day === yesterday) return 'Yesterday';
  return new Date(`${day}T12:00:00`).toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    year: day.slice(0, 4) === today.slice(0, 4) ? undefined : 'numeric',
  });
}
