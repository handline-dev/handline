// task.discard of a batch, as the daemon serves it: every task checked first, so all of them are discarded or none, each under its own
// reason (reasons, keyed "2" or "T2") or the shared one.
import { TaskFailure, type FakeTasks } from '#src/tasks.ts';

export function discardAll(tasks: FakeTasks, p: Record<string, unknown>): void {
  if (!Array.isArray(p.tasks) || p.tasks.length === 0) throw new TaskFailure('bad_request', 'tasks is a list of task numbers');
  const reasons = (p.reasons ?? {}) as Record<string, unknown>;
  const each = p.tasks.map((task: unknown) => ({ task, reason: reasons[String(task)] ?? reasons[`T${String(task)}`] ?? p.reason }));
  for (const e of each) {
    const t = tasks.find(e.task);
    if (!String(e.reason ?? '').trim()) throw new TaskFailure('bad_request', `task ${t.id} needs a reason`);
    if (t.state === 'done' || t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is ${t.state}`);
  }
  for (const e of each) tasks.discard(e);
}
