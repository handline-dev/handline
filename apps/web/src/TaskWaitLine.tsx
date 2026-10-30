// The task page's line under the chips: what the task waits on and what waits on it, each a link, then the Graph.
import { taskRef } from '@handline/app-core';
import type { TaskDetail } from '@handline/client';
import { useTasks } from '@handline/app-data';
import { Id } from '#src/ds/Link.tsx';
import { goTasks } from '#src/route.ts';

export function TaskWaitLine({ t }: { t: TaskDetail }) {
  const blocks = (useTasks({}).data?.items ?? []).filter((x) => x.blocked_by?.includes(t.id));
  if (!t.blockers.length && !blocks.length) return null;
  return (
    <p className="twait m-0 mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm text-ink-3" data-wait>
      {t.blockers.length ? (
        <span data-waiting-on>
          Waiting on{' '}
          {t.blockers.map((b) => (
            <Id key={b.task} href={`#/tasks/${b.task}`} title={`${b.title}: ${b.reason}`} className="mr-1.5">
              {taskRef(b.task)}
            </Id>
          ))}
        </span>
      ) : null}
      {blocks.length ? (
        <span data-blocks>
          Blocks{' '}
          {blocks.map((b) => (
            <Id key={b.id} href={`#/tasks/${b.id}`} className="mr-1.5">
              {taskRef(b.id)}
            </Id>
          ))}
        </span>
      ) : null}
      <a href="#/tasks/graph" className="text-acc-ink" onClick={(e) => (e.preventDefault(), goTasks('graph'))}>
        Graph
      </a>
    </p>
  );
}
