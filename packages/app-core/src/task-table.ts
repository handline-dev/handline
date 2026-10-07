// What the Tasks page draws, decided without a screen: which rows pass the filters, in what order, as a list or as a tree, and the
// idle and timeout words of a row. The columns and the default states follow the bridge's tasks_view.py (`HEADS`, `LISTED`).
import type { Task } from '@handline/client';

export const TASK_STATES = ['pending', 'draft', 'queued', 'doing', 'blocked', 'done', 'discarded'] as const;
/** What a list shows when it opens: done and discarded are out of the way until asked for. */
export const LISTED: readonly string[] = TASK_STATES.filter((s) => s !== 'done' && s !== 'discarded');
export const CLOSED: readonly string[] = ['done', 'discarded'];

export type TaskColumn = 'number' | 'priority' | 'state' | 'title' | 'owner' | 'idle' | 'timeout' | 'asker';
export const TASK_COLUMNS: readonly TaskColumn[] = ['number', 'priority', 'state', 'title', 'owner', 'idle', 'timeout', 'asker'];

export interface TaskFilter {
  states: ReadonlySet<string>;
  /** Tasks this agent owns or asked for; '' is everyone's. */
  agent: string;
  closed: boolean;
}
export const defaultFilter = (): TaskFilter => ({ states: new Set(LISTED), agent: '', closed: false });

export function shown(tasks: readonly Task[], f: TaskFilter): Task[] {
  return tasks.filter(
    (t) =>
      (CLOSED.includes(t.state) ? f.closed : f.states.has(t.state)) && (!f.agent || t.to === f.agent || t.from === f.agent),
  );
}

const MS = { m: 60_000, h: 3_600_000, d: 86_400_000 } as const;
/** "90m", "2h", "1h30m", "4d" as milliseconds; null when it is not a duration. */
export function durationMs(d: string | null): number | null {
  if (!d) return null;
  const m = /^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$/.exec(d.trim());
  if (!m || !m[0]) return null;
  return Number(m[1] ?? 0) * MS.d + Number(m[2] ?? 0) * MS.h + Number(m[3] ?? 0) * MS.m;
}
const span = (ms: number) => {
  const m = Math.round(ms / MS.m);
  if (m < 1) return '<1m';
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}h${m % 60}m` : `${h}h`;
  return `${Math.floor(h / 24)}d`;
};

/** How long a task has been waiting where it is: since it was delivered, or created while it was not. */
export const idleMs = (t: Task, now: number): number => Math.max(0, now - Date.parse(t.delivered ?? t.created));
export const idleText = (t: Task, now: number): string => (CLOSED.includes(t.state) ? '' : span(idleMs(t, now)));

/** "4h" for a task not running yet, "1h12m left" while it does, "over by 20m" past its time box. */
export function timeoutText(t: Task, now: number): string {
  const box = durationMs(t.time_box);
  if (box === null || CLOSED.includes(t.state)) return '';
  if (t.state !== 'doing' || !t.delivered) return t.time_box ?? '';
  const left = box - (now - Date.parse(t.delivered));
  return left >= 0 ? `${span(left)} left` : `over by ${span(-left)}`;
}

export const isOver = (t: Task, now: number): boolean => timeoutText(t, now).startsWith('over');

const value = (t: Task, c: TaskColumn, now: number): string | number => {
  switch (c) {
    case 'number':
      return t.id;
    case 'priority':
      return t.priority ?? 2;
    case 'state':
      return TASK_STATES.indexOf(t.state as never) + 1 || 99;
    case 'title':
      return t.title.toLowerCase();
    case 'owner':
      return t.to;
    case 'asker':
      return t.from;
    case 'idle':
      return idleMs(t, now);
    case 'timeout':
      return durationMs(t.time_box) ?? 0;
  }
};

/** A sorted copy. Ties fall back to the number, so a refresh never shuffles equal rows. */
export function sortTasks(tasks: readonly Task[], by: TaskColumn, reverse: boolean, now: number): Task[] {
  const sign = reverse ? -1 : 1;
  return [...tasks].sort((a, b) => {
    const x = value(a, by, now);
    const y = value(b, by, now);
    return (x < y ? -1 : x > y ? 1 : 0) * sign || a.id - b.id;
  });
}

export interface TreeRow {
  task: Task;
  depth: number;
  /** A parent that the filter hides, drawn dimmed so the chain still reads. */
  dim: boolean;
}

/**
 * The tasks as a tree under their parents. A parent outside the filter stays as a dimmed row when it holds a shown task; a
 * parent that is not in `all` at all leaves its child at the top. Children keep the order of `rows`.
 */
export function taskTree(rows: readonly Task[], all: readonly Task[]): TreeRow[] {
  const byId = new Map(all.map((t) => [t.id, t]));
  const inRows = new Set(rows.map((t) => t.id));
  const keep = new Set(inRows);
  for (const t of rows) for (let p = t.parent; p !== null && byId.has(p) && !keep.has(p); p = byId.get(p)!.parent) keep.add(p);
  const kids = new Map<number | null, Task[]>();
  for (const id of keep) {
    const t = byId.get(id)!;
    const up = t.parent !== null && keep.has(t.parent) ? t.parent : null;
    kids.set(up, [...(kids.get(up) ?? []), t]);
  }
  // A row sorts where it is; a parent that is only there for the chain sorts where its first shown descendant does.
  const order = new Map<number, number>();
  rows.forEach((t, i) => {
    for (let n: Task | undefined = t; n && keep.has(n.id); n = n.parent === null ? undefined : byId.get(n.parent)) if (!order.has(n.id)) order.set(n.id, i);
  });
  const out: TreeRow[] = [];
  const walk = (up: number | null, depth: number) => {
    const list = (kids.get(up) ?? []).sort((a, b) => (order.get(a.id) ?? 1e9) - (order.get(b.id) ?? 1e9) || a.id - b.id);
    for (const t of list) {
      out.push({ task: t, depth, dim: !inRows.has(t.id) });
      walk(t.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

/** The agents named on any task, for the agent filter. */
export const taskAgents = (tasks: readonly Task[]): string[] => [...new Set(tasks.flatMap((t) => [t.to, t.from]))].sort();
