// The writes behind the task verbs, shared by the web list and the phone's screens, and the one table that says what each verb sends per task.
import type { RowVerb } from '@handline/app-core';
import { useWrite } from '#src/write.ts';

/** One mutation per task write, and `verbs`: what each row verb sends for a task; `arg` is the sentence, owner, priority or time box it asks for. */
export function useTaskActions() {
  const w = {
    edit: useWrite('task.edit'),
    discard: useWrite('task.discard'),
    merge: useWrite('task.merge'),
    done: useWrite('task.done'),
    reopen: useWrite('task.reopen'),
    queue: useWrite('task.queue'),
    draft: useWrite('task.draft'),
    release: useWrite('task.release'),
    withdraw: useWrite('task.withdraw'),
    cancel: useWrite('task.cancel'),
  };
  const verbs: Record<RowVerb, (task: number, arg: string) => Promise<unknown>> = {
    done: (task, evidence) => w.done.mutateAsync({ task, evidence, reason: true }),
    reopen: (task, note) => w.reopen.mutateAsync({ task, ...(note ? { note } : {}) }),
    queue: (task) => w.queue.mutateAsync({ tasks: [task] }),
    draft: (task) => w.draft.mutateAsync({ tasks: [task] }),
    release: (task, reason) => w.release.mutateAsync({ task, reason }),
    withdraw: (task) => w.withdraw.mutateAsync({ task }),
    cancel: (task, reason) => w.cancel.mutateAsync({ task, ...(reason ? { reason } : {}) }),
    discard: (task, reason) => w.discard.mutateAsync({ task, reason }),
    owner: (task, owner) => w.edit.mutateAsync({ task, owner }),
    priority: (task, p) => w.edit.mutateAsync({ task, priority: Number(p) as 1 | 2 | 3 }),
    timebox: (task, time_box) => w.edit.mutateAsync({ task, time_box }),
  };
  return { ...w, verbs };
}
