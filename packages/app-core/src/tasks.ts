// What a task looks like right after the person's verb, before the daemon has answered: the pure half of an optimistic task write.
import type { Task } from '@handline/client';

export type TaskVerb = 'done' | 'queue' | 'draft' | 'edit' | 'reopen' | 'release' | 'withdraw' | 'cancel';
export interface TaskEdit {
  owner?: string;
  title?: string;
  time_box?: string;
  priority?: 1 | 2 | 3;
}

/**
 * The task as the verb leaves it, or undefined when the transition is not one this knows for certain
 * (a verb the daemon decides, such as reopen or release). Then the write shows a pending mark and changes nothing.
 */
export function taskAfter(verb: TaskVerb, task: Task, now: string, edit: TaskEdit = {}): Task | undefined {
  switch (verb) {
    case 'done':
      return task.state === 'doing' || task.state === 'queued' ? { ...task, state: 'done', done: now } : undefined;
    case 'queue':
      return task.state === 'draft' ? { ...task, state: 'queued' } : undefined;
    case 'draft':
      return task.state === 'queued' ? { ...task, state: 'draft' } : undefined;
    case 'edit':
      return {
        ...task,
        ...(edit.owner ? { to: edit.owner } : {}),
        ...(edit.title ? { title: edit.title } : {}),
        ...(edit.time_box ? { time_box: edit.time_box } : {}),
        ...(edit.priority ? { priority: edit.priority } : {}),
      };
    default:
      return undefined; // reopen, release, withdraw and cancel: where the daemon puts the task depends on more than this knows
  }
}

/** How a task is named wherever it is shown: T<id>. A message is #<id>; a task is never written with #. */
export const taskRef = (id: number): string => `T${id}`;

export interface AgentTask {
  task: Task;
  /** `owner`: the agent holds it (a task it opened for itself too); `assigned`: the agent opened it and `task.to` holds it. */
  role: 'owner' | 'assigned';
}

const ENDED = ['done', 'discarded'];

/** The text and tone of a task's due clock at `now`: over once the daemon says so (`overdue_since`) or the end has passed; null when it has no clock or has ended. */
export function dueOf(t: Task, now = Date.now()): { text: string; over: boolean } | null {
  const end = t.time?.end ? Date.parse(t.time.end) : NaN;
  if (ENDED.includes(t.state) || Number.isNaN(end)) return null;
  const since = t.time?.overdue_since ? Date.parse(t.time.overdue_since) : NaN;
  const over = !Number.isNaN(since) || end <= now;
  const s = Math.abs(Math.round(((over && !Number.isNaN(since) ? since : end) - now) / 1000));
  const span = s < 60 ? '<1m' : s < 3600 ? `${Math.floor(s / 60)}m` : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`;
  return over ? { text: `${span} overdue`, over: true } : { text: `${span} left`, over: false };
}

/** Every task the agent holds or opened, once each: open ones first (overdue before the rest), then newest id first. */
export function agentTasks(items: readonly Task[], agent: string, now = Date.now()): AgentTask[] {
  const rank = (t: Task) => (ENDED.includes(t.state) ? 2 : dueOf(t, now)?.over ? 0 : 1);
  const own = new Map(items.filter((t) => t.to === agent || t.from === agent).map((t) => [t.id, t]));
  return [...own.values()]
    .map((task): AgentTask => ({ task, role: task.to === agent ? 'owner' : 'assigned' }))
    .sort((a, b) => rank(a.task) - rank(b.task) || b.task.id - a.task.id);
}
