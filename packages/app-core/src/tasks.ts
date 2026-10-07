// What a task looks like right after the person's verb, before the daemon has answered: the pure half of an optimistic task write.
import type { Task } from '@handline/client';

export type TaskVerb = 'done' | 'queue' | 'draft' | 'edit' | 'reopen' | 'release' | 'withdraw' | 'cancel';
export interface TaskEdit {
  owner?: string;
  title?: string;
  time_box?: string;
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
      };
    default:
      return undefined; // reopen, release, withdraw and cancel: where the daemon puts the task depends on more than this knows
  }
}
