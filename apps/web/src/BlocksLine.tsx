// "Blocks T7016 T7013" under a request's title: the tasks waiting on this one, so the person answers the blocking request first.
import { taskRef } from '@handline/app-core';
import { useTasks } from '@handline/app-data';

export function BlocksLine({ task }: { task: number | null | undefined }) {
  const blocked = (useTasks({}).data?.items ?? []).filter((t) => task != null && t.blocked_by?.includes(task));
  if (!blocked.length) return null;
  return (
    <p className="m-0 mt-1 text-xs text-ink-3" data-blocks>
      Blocks {blocked.map((t) => taskRef(t.id)).join(' ')}
    </p>
  );
}
