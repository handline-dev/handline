// The Tasks tab of an agent's workspace: the tasks this agent holds, newest first, open ones above the closed; a click opens the task in
// the same pane (the task detail of the Tasks page) and Back returns to the list.
import { useMemo, useState } from 'react';
import { useServes, useTasks } from '@handline/app-data';
import { Icon } from './Icon.tsx';
import { StateChip } from './taskui.tsx';
import { TaskDetail } from './TaskDetail.tsx';
import { View } from './View.tsx';

const CLOSED = ['done', 'discarded'];

export function TasksTab({ agent }: { agent: string }) {
  const q = useTasks({ all: true });
  const { served } = useServes('tasks.list');
  const [open, setOpen] = useState<number | null>(null);
  const mine = useMemo(
    () =>
      (q.data?.items ?? [])
        .filter((t) => t.to === agent)
        .sort((a, b) => Number(CLOSED.includes(a.state)) - Number(CLOSED.includes(b.state)) || b.id - a.id),
    [q.data, agent],
  );
  if (open !== null) return <TaskDetail id={open} onBack={() => setOpen(null)} embedded />;
  return (
    <section className="tasks-tab" aria-label={`Tasks of ${agent}`}>
      <View q={q} served={served} shape="tasks" what="the tasks" empty={!mine.length && `${agent} holds no tasks.`}>
        <ul className="tasks-tab-list">
          {mine.map((t) => (
            <li key={t.id}>
              <button
                className={`tasks-tab-row${CLOSED.includes(t.state) ? ' closed' : ''}`}
                data-task={t.id}
                onClick={() => setOpen(t.id)}
              >
                <Icon name="tasks" size={14} label="Task" />
                <span className="mono">#{t.id}</span>
                <span className="ttitle">{t.title}</span>
                <StateChip state={t.state} />
              </button>
            </li>
          ))}
        </ul>
      </View>
    </section>
  );
}
