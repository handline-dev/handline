// The writes of the Tasks list that act on several rows, and what is in flight for each row: an authored write shows its new value with a
// hollow dot, a decided one shows "…" where the state is, and a refused row says "not changed" for six seconds.
import { useState } from 'react';
import { useStore } from '@handline/app-react';
import { usePending, useWrite } from '@handline/app-data';

const idsOf = (v: { task?: number; tasks?: number[] }): number[] => [...(v.task === undefined ? [] : [v.task]), ...(v.tasks ?? [])];

export function useBulk(afterwards: () => void) {
  const store = useStore();
  const edit = useWrite('task.edit');
  const discard = useWrite('task.discard');
  const merge = useWrite('task.merge');
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
  /** One write per task; a partial failure leaves the refused rows marked and says "2 of 3 changed". */
  async function each(ids: number[], what: string, run: (id: number) => Promise<unknown>) {
    const results = await Promise.allSettled(ids.map(run));
    const bad = ids.filter((_, i) => results[i]!.status === 'rejected');
    if (bad.length) {
      flash(bad);
      if (bad.length < ids.length) store.reportError(what, new Error(`${ids.length - bad.length} of ${ids.length} changed`));
    }
    afterwards();
  }
  return {
    failed,
    authored,
    decided,
    owner: (ids: number[], owner: string) => each(ids, 'owner', (task) => edit.mutateAsync({ task, owner })),
    priority: (ids: number[], priority: 1 | 2 | 3) => each(ids, 'priority', (task) => edit.mutateAsync({ task, priority })),
    discard: (ids: number[], reason: string) => each(ids, 'discard', (task) => discard.mutateAsync({ task, reason })),
    merge: (into: number, tasks: number[]) => each(tasks, 'merge', () => merge.mutateAsync({ into, tasks })),
  };
}
