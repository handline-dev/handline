// What can be done to a task from the Tasks views, one or many: which tasks a verb applies to, one write per task so each has its own
// result, and a selection with shift-click ranges. The daemon decides what is allowed; this only skips what could never apply.
import type { Task } from '@handline/client';
import { CLOSED } from './task-table.ts';

export type RowVerb =
  'done' | 'reopen' | 'queue' | 'draft' | 'release' | 'withdraw' | 'cancel' | 'discard' | 'owner' | 'priority' | 'timebox';

/** Each verb as its menu word and the daemon method that serves it (owner, priority and time box are all `task.edit`). */
export const TASK_VERBS: readonly { verb: RowVerb; label: string; method: string }[] = [
  { verb: 'done', label: 'Done', method: 'task.done' },
  { verb: 'queue', label: 'Queue', method: 'task.queue' },
  { verb: 'draft', label: 'Back to draft', method: 'task.draft' },
  { verb: 'release', label: 'Release', method: 'task.release' },
  { verb: 'reopen', label: 'Reopen', method: 'task.reopen' },
  { verb: 'owner', label: 'Change owner', method: 'task.edit' },
  { verb: 'priority', label: 'Set priority', method: 'task.edit' },
  { verb: 'timebox', label: 'Set time box', method: 'task.edit' },
  { verb: 'withdraw', label: 'Withdraw', method: 'task.withdraw' },
  { verb: 'cancel', label: 'Cancel', method: 'task.cancel' },
  { verb: 'discard', label: 'Discard', method: 'task.discard' },
];

const open = (t: Task) => !CLOSED.includes(t.state);
const startable = (t: Task) => ['draft', 'queued', 'pending'].includes(t.state);

/** Why a verb cannot apply to a task (as `me` sees it), or null when it can. */
export function whyNot(verb: RowVerb, t: Task, me: string): string | null {
  const closed = `#${t.id} is already closed`;
  switch (verb) {
    case 'done':
    case 'cancel':
    case 'discard':
    case 'owner':
    case 'priority':
    case 'timebox':
      return open(t) ? null : closed;
    case 'queue':
      return t.state === 'draft' ? null : `#${t.id} is not a draft`;
    case 'draft':
      return t.state === 'queued' ? null : `#${t.id} is not queued`;
    case 'release':
      return t.state === 'doing' ? null : `#${t.id} is not running`;
    case 'reopen':
      return t.state === 'done' && t.from === me ? null : `#${t.id} is not a finished task you asked`;
    case 'withdraw':
      return startable(t) && t.from === me ? null : `#${t.id} was not asked by you or has started`;
  }
}

/** The tasks a verb will run on and the ones it skips, each with its reason. */
export function plan(verb: RowVerb, tasks: Task[], me: string): { run: Task[]; skip: { task: Task; why: string }[] } {
  const run: Task[] = [];
  const skip: { task: Task; why: string }[] = [];
  for (const task of tasks) {
    const why = whyNot(verb, task, me);
    if (why) skip.push({ task, why });
    else run.push(task);
  }
  return { run, skip };
}

export interface Outcome {
  task: number;
  ok: boolean;
  why?: string;
}

/** One write per task, all at once; a refusal is that task's outcome and does not stop the others. */
export async function applyEach(ids: number[], run: (id: number) => Promise<unknown>): Promise<Outcome[]> {
  const settled = await Promise.allSettled(ids.map(run));
  return settled.map((s, i) =>
    s.status === 'fulfilled'
      ? { task: ids[i]!, ok: true }
      : { task: ids[i]!, ok: false, why: s.reason instanceof Error ? s.reason.message : String(s.reason) },
  );
}

/** "3 of 5 changed", or "all 5 changed" / "none of 5 changed". */
export function summary(outcomes: Outcome[]): string {
  const ok = outcomes.filter((o) => o.ok).length;
  return ok === outcomes.length
    ? `all ${ok} changed`
    : ok === 0
      ? `none of ${outcomes.length} changed`
      : `${ok} of ${outcomes.length} changed`;
}

/** The ids from one to the other in the shown order, both ends included; just `to` when `from` is not shown. */
export function rangeBetween(order: number[], from: number | null, to: number): number[] {
  const a = from === null ? -1 : order.indexOf(from);
  const b = order.indexOf(to);
  if (b < 0) return [];
  if (a < 0) return [to];
  return order.slice(Math.min(a, b), Math.max(a, b) + 1);
}
