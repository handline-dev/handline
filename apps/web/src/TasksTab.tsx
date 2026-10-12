// The Tasks tab of an agent's workspace: the tasks this agent holds, newest first, open ones above the closed; a click opens the task in
// the same pane (the task detail of the Tasks page) and Back returns to the list.
import { useMemo } from 'react';
import { openTask, taskRef, type RowVerb } from '@handline/app-core';
import { useServes, useTasks } from '@handline/app-data';
import { Icon } from './Icon.tsx';
import { StateChip } from './taskui.tsx';
import { TaskDetail } from './TaskDetail.tsx';
import { useAgents } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { Button, Toolbar } from './ui/index.ts';
import { VerbMenu, VerbSheets, useVerbCans, useVerbFlow } from './TaskVerbUi.tsx';
import { useBulk } from './taskActions.ts';
import { useMarks } from './useMarks.ts';
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
  const me = useStore().me;
  const cans = useVerbCans();
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  const { marked, setMarked, toggle, toggleAll } = useMarks(mine.map((t) => t.id));
  const bulk = useBulk(() => setMarked(new Set()));
  const verbs = useVerbFlow({ all: mine, me, agents, bulk, focus: () => undefined });
  /** The row's menu acts on that row, or on the marked rows when it is one of them. */
  const rowVerb = (id: number, verb: RowVerb) => {
    if (!marked.has(id)) setMarked(new Set([id]));
    verbs.pick(verb);
  };
  if (open !== null) return <TaskDetail id={open} onBack={() => setOpen(null)} embedded />;
  return (
    <section className="tasks-tab h-full overflow-auto p-2" aria-label={`Tasks of ${agent}`}>
      <View q={q} served={served} shape="tasks" what="the tasks" empty={!mine.length && `${agent} holds no tasks.`}>
        <ul className="tasks-tab-list m-0 flex list-none flex-col gap-1 p-0">
          {mine.map((t) => (
            <li key={t.id} className="flex items-center gap-1.5">
              <input
                type="checkbox"
                aria-label={`Mark ${taskRef(t.id)}`}
                checked={marked.has(t.id)}
                onChange={() => undefined}
                onClick={(e) => toggle(t.id, e.shiftKey)}
              />
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
              <VerbMenu tasks={[t]} me={me} cans={cans} onPick={(v) => rowVerb(t.id, v)} />
            </li>
          ))}
        </ul>
      </View>
      <VerbSheets flow={verbs} ids={[...marked]} />
      {marked.size ? (
        <Toolbar label="Actions on the marked tasks">
          <span className="muted mr-auto">{marked.size} marked</span>
          <Button variant="outline" size="sm" label={marked.size >= mine.length ? 'Clear' : 'Select all'} onClick={toggleAll}>
            {marked.size >= mine.length ? 'Clear' : `Select all ${mine.length}`}
          </Button>
          <VerbMenu tasks={verbs.byId([...marked])} me={me} cans={cans} onPick={verbs.pick} title="Actions on the marked tasks" up />
        </Toolbar>
      ) : null}
    </section>
  );
}
