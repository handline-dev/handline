// The Tasks tab of an agent's workspace: the tasks this agent holds, newest first, open ones above the closed; a click opens the task in
// the same pane (the task detail of the Tasks page) and Back returns to the list.
import { useMemo } from 'react';
import { openTask, taskRef } from '@handline/app-core';
import { useServes, useTasks } from '@handline/app-data';
import { Icon } from './Icon.tsx';
import { StateChip } from './taskui.tsx';
import { TaskDetail } from './TaskDetail.tsx';
import { View } from './View.tsx';
import { useWorkspace } from './Workspace.tsx';

const CLOSED = ['done', 'discarded'];

export function TasksTab({ agent, task = null }: { agent: string; task?: number | null }) {
  const q = useTasks({ all: true });
  const { served } = useServes('tasks.list');
  const { apply } = useWorkspace();
  const open = task; // the open task lives in the tab's params, so a chat line can open one on a tab already there
  const setOpen = (id: number | null) => apply((w) => openTask(w, id));
  const mine = useMemo(
    () =>
      (q.data?.items ?? [])
        .filter((t) => t.to === agent)
        .sort((a, b) => Number(CLOSED.includes(a.state)) - Number(CLOSED.includes(b.state)) || b.id - a.id),
    [q.data, agent],
  );
  if (open !== null) return <TaskDetail id={open} onBack={() => setOpen(null)} embedded />;
  return (
    <section className="tasks-tab h-full overflow-auto p-2" aria-label={`Tasks of ${agent}`}>
      <View q={q} served={served} shape="tasks" what="the tasks" empty={!mine.length && `${agent} holds no tasks.`}>
        <ul className="tasks-tab-list m-0 flex list-none flex-col gap-1 p-0">
          {mine.map((t) => (
            <li key={t.id}>
              <button
                className={`tasks-tab-row flex w-full cursor-pointer items-center gap-2 rounded-card border border-line bg-panel px-2.5 py-1.5 text-left${CLOSED.includes(t.state) ? ' closed opacity-60' : ''}`}
                data-task={t.id}
                onClick={() => setOpen(t.id)}
              >
                <Icon name="tasks" size={14} label="Task" />
                <span className="mono">{taskRef(t.id)}</span>
                <span className="ttitle min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap text-body font-medium">
                  {t.title}
                </span>
                <StateChip state={t.state} />
              </button>
            </li>
          ))}
        </ul>
      </View>
    </section>
  );
}
