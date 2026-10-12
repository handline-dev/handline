// The writes of the Tasks list that act on several rows, and what is in flight for each row: an authored write shows its new value with a
// hollow dot, a decided one shows "…" where the state is, and a refused row says "not changed" for six seconds.
import { useState } from 'react';
import { useStore } from '@handline/app-react';
import { usePending, useWrite } from '@handline/app-data';
import { applyEach, type Outcome, type RowVerb } from '@handline/app-core';

const idsOf = (v: { task?: number; tasks?: number[] }): number[] => [...(v.task === undefined ? [] : [v.task]), ...(v.tasks ?? [])];

export function useBulk(afterwards: () => void) {
  const store = useStore();
  const edit = useWrite('task.edit');
  const discard = useWrite('task.discard');
  const merge = useWrite('task.merge');
  const done = useWrite('task.done');
  const reopen = useWrite('task.reopen');
  const queue = useWrite('task.queue');
  const draft = useWrite('task.draft');
  const release = useWrite('task.release');
  const withdraw = useWrite('task.withdraw');
  const cancel = useWrite('task.cancel');
  const [result, setResult] = useState<{ what: string; outcomes: Outcome[] } | null>(null);
  const [failed, setFailed] = useState<ReadonlySet<number>>(new Set());
  const authored = new Set(
    [...usePending('task.edit'), ...usePending('task.done'), ...usePending('task.queue'), ...usePending('task.draft')].flatMap(idsOf),
  );
  const decided = new Set(
    [...usePending('task.withdraw'), ...usePending('task.cancel'), ...usePending('task.discard'), ...usePending('task.reopen')].flatMap(
      idsOf,
    ),
  );

  const flash = (ids: number[]) => {
    setFailed((s) => new Set([...s, ...ids]));
    setTimeout(() => setFailed((s) => new Set([...s].filter((i) => !ids.includes(i)))), 6000);
  };
  /** One write per task; the outcomes of a batch (or any refusal) are kept for the result sheet, and a partial failure says "2 of 3 changed". */
  async function each(ids: number[], what: string, run: (id: number) => Promise<unknown>) {
    const outcomes = await applyEach(ids, run);
    const bad = outcomes.filter((o) => !o.ok).map((o) => o.task);
    if (bad.length) {
      flash(bad);
      if (bad.length < ids.length) store.reportError(what, new Error(`${ids.length - bad.length} of ${ids.length} changed`));
    }
    if (bad.length || ids.length > 1) setResult({ what, outcomes });
    afterwards();
    return outcomes;
  }
  /** `arg` is the sentence, owner, priority or time box the verb asks for; the verbs without one ignore it. */
  const verbs: Record<RowVerb, (task: number, arg: string) => Promise<unknown>> = {
    done: (task, evidence) => done.mutateAsync({ task, evidence, reason: true }),
    reopen: (task, note) => reopen.mutateAsync({ task, ...(note ? { note } : {}) }),
    queue: (task) => queue.mutateAsync({ tasks: [task] }),
    draft: (task) => draft.mutateAsync({ tasks: [task] }),
    release: (task, to) => release.mutateAsync({ task, to: to === 'draft' ? 'draft' : 'queued' }),
    withdraw: (task) => withdraw.mutateAsync({ task }),
    cancel: (task, reason) => cancel.mutateAsync({ task, ...(reason ? { reason } : {}) }),
    discard: (task, reason) => discard.mutateAsync({ task, reason }),
    owner: (task, owner) => edit.mutateAsync({ task, owner }),
    priority: (task, p) => edit.mutateAsync({ task, priority: Number(p) as 1 | 2 | 3 }),
    timebox: (task, time_box) => edit.mutateAsync({ task, time_box }),
  };
  return {
    failed,
    result,
    clearResult: () => setResult(null),
    run: (verb: RowVerb, ids: number[], arg = '') => each(ids, verb, (id) => verbs[verb](id, arg)),
    authored,
    decided,
    owner: (ids: number[], owner: string) => each(ids, 'owner', (task) => edit.mutateAsync({ task, owner })),
    priority: (ids: number[], priority: 1 | 2 | 3) => each(ids, 'priority', (task) => edit.mutateAsync({ task, priority })),
    discard: (ids: number[], reason: string) => each(ids, 'discard', (task) => discard.mutateAsync({ task, reason })),
    merge: async (into: number, tasks: number[]) => {
      try {
        await merge.mutateAsync({ into, tasks });
      } catch {
        flash(tasks);
      }
      afterwards();
    },
  };
}
