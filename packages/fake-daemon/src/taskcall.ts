// The fake daemon's task methods: which ones it serves and how one call is answered by the store.
import { invalidParams, refused, storeFailure } from '#src/errors.ts';
import { discardAll } from '#src/discardbatch.ts';
import { timeOf } from '#src/history.ts';
import { TaskFailure, type FakeTasks } from '#src/tasks.ts';

/** Answers one task method, turning the store's refusals into the daemon's errors. */
export function taskCall(
  tasks: FakeTasks,
  method: string,
  p: Record<string, unknown>,
  person: string,
  agent: boolean,
  script: { accepted(said: string, extra?: Record<string, unknown>): unknown },
  ask: (task: number, to: string, text: string) => number = () => {
    throw refused('unsupported', 'the fake daemon does not serve task.ask');
  },
): unknown {
  const ok = (said: string, extra?: Record<string, unknown>) => script.accepted(said, extra);
  tasks.by = person;
  try {
    switch (method) {
      case 'task.history':
        return tasks.history(p);
      case 'task.time':
        return timeOf(p.task, tasks);
      case 'tasks.list':
        return tasks.list(p);
      case 'task.get':
        return tasks.get(p, agent);
      case 'task.comment.list':
        return tasks.comments(p, agent);
      case 'task.new':
        return ok('Task asked.', { task: tasks.create(p, person, agent) });
      case 'task.edit':
        return (tasks.edit(p, agent), ok('Task changed.'));
      case 'task.withdraw':
        return (tasks.close(p, 'withdraw'), ok('Task withdrawn.'));
      case 'task.cancel':
        return (tasks.close(p, 'cancel'), ok('Task cancelled.'));
      case 'task.ask': {
        if (!Number.isInteger(p.task)) throw invalidParams('task is a task number', 'task');
        if (typeof p.to !== 'string' || !p.to) throw invalidParams("to is an agent's name", 'to');
        if (typeof p.text !== 'string' || !p.text.trim()) throw invalidParams('text is the question, not empty', 'text');
        return ok('Asked.', { request: ask(tasks.find(p.task).id, p.to, p.text.trim()) });
      }
      case 'task.done':
        return ok(tasks.done(p));
      case 'task.reopen':
        return (tasks.reopen(p, person), ok('Task reopened.'));
      case 'task.queue':
        return (tasks.move(p, ['draft'], 'queued'), ok('Queued.'));
      case 'task.draft':
        return (tasks.move(p, ['queued', 'doing'], 'draft'), ok('Back to draft.'));
      case 'task.discard':
        return (Array.isArray(p.tasks) ? discardAll(tasks, p) : tasks.discard(p), ok('Task discarded.'));
      case 'task.release':
        return (tasks.release(p), ok('Task released.'));
      case 'task.merge':
        return (tasks.merge(p), ok('Tasks folded.'));
      case 'task.comment.add':
        return ok('Comment added.', { memo: tasks.memo(p, 'add', person, agent) });
      case 'task.comment.edit':
        return (tasks.memo(p, 'edit', person, agent), ok('Comment changed.'));
      case 'task.comment.delete':
        return (tasks.memo(p, 'delete', person), ok('Comment deleted.'));
      default:
        throw refused('unsupported', `the fake daemon does not serve ${method}`);
    }
  } catch (e) {
    if (!(e instanceof TaskFailure)) throw e;
    throw storeFailure(e);
  }
}
