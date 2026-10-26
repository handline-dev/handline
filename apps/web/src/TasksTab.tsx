// The Tasks tab of an agent's workspace: the tasks this agent holds or opened, overdue first, then open ones, then the closed; a click opens the task in
// the same pane (the task detail of the Tasks page); the window's Back returns to the list. Rows share one grid of fixed columns, so a long title is cut
// with an ellipsis and never widens the row; boxes appear only in Select mode; older tasks come 100 at a time on a button.
import { useEffect, useMemo, useState } from 'react';
import { agentTasks, dueOf, openTask, taskRef, watchResult, type RowVerb } from '@handline/app-core';
import { TASK_PAGE, useAgentTasks, useServes } from '@handline/app-data';
import { Icon } from '#src/Icon.tsx';
import { StateChip, useNow } from '#src/taskui.tsx';
import { TaskDetail } from '#src/TaskDetail.tsx';
import { useAgents } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { Button, Toolbar, Check, Segmented } from '#src/ui/index.ts';
import { TaskColumns } from '#src/TaskGraph.tsx';
import { RowMenu, VerbMenu, VerbSheets, useVerbCans, useVerbFlow } from '#src/TaskVerbUi.tsx';
import { useBulk } from '#src/taskActions.ts';
import { useMarks } from '#src/useMarks.ts';
import { View } from '#src/View.tsx';
import { useWorkspace } from '#src/Workspace.tsx';

const CLOSED = ['done', 'discarded'];
const bare = (agent: string) => agent.split('.').at(-1) ?? agent; // 'handline.daemon' is 'daemon' in a row: the team is the same on every line
// state · number · title (the one column that gives) · owner · time
const COLS = 'grid-cols-[1.75rem_3.25rem_minmax(0,1fr)_6.5rem_5rem]';

export function TasksTab({ agent, task = null }: { agent: string; task?: number | null }) {
  const { query: q, items, more, loadOlder, loadingOlder } = useAgentTasks(agent);
  const [selecting, setSelecting] = useState(false);
  const [view, setView] = useState<'list' | 'graph'>('list');
  const now = useNow();
  const { served } = useServes('tasks.list');
  const { apply } = useWorkspace();
  const open = task; // the open task lives in the tab's params, so a chat line can open one on a tab already there
  const setOpen = (id: number | null) => apply((w) => openTask(w, id));
  const rows = useMemo(() => agentTasks(items, agent, now), [items, agent, now]);
  const mine = useMemo(() => rows.map((r) => r.task), [rows]);
  // The agent's own tasks, and the ones they wait on, so an arrow can leave the column.
  const graphTasks = useMemo(() => {
    const held = mine.filter((t) => t.to === agent);
    const waited = new Set(held.flatMap((t) => t.blocked_by ?? []));
    return mine.filter((t) => t.to === agent || waited.has(t.id));
  }, [mine, agent]);
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
  useEffect(() => {
    if (!selecting) return;
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && (setSelecting(false), setMarked(new Set()));
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [selecting, setMarked]);
  const endSelect = () => (setSelecting(false), setMarked(new Set()));
  if (open !== null) return <TaskDetail id={open} embedded />;
  return (
    <section className="tasks-tab h-full overflow-auto p-2" aria-label={`Tasks of ${agent}`}>
      <View q={q} served={served} shape="tasks" what="the tasks" empty={!mine.length && `${agent} holds and has opened no tasks.`}>
        <div className="mb-1 flex items-center justify-between">
          <Segmented
            label="View"
            value={view}
            onChange={setView}
            options={[
              { value: 'list', label: 'List' },
              { value: 'graph', label: 'Graph' },
            ]}
          />
          {view === 'graph' ? null : selecting ? (
            <Button variant="outline" size="sm" label="Done selecting" aria-label="Done selecting" onClick={endSelect}>
              Done
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              icon="select"
              label="Select tasks"
              aria-label="Select tasks"
              onClick={() => setSelecting(true)}
            >
              Select
            </Button>
          )}
        </div>
        {view === 'graph' ? (
          <div className="flex h-[calc(100%-2rem)] min-h-80 flex-col">
            <TaskColumns tasks={graphTasks} now={now} me={me} scopeKey={agent} />
          </div>
        ) : (
          <ul className="tasks-tab-list m-0 flex list-none flex-col gap-1 p-0">
            {rows.map(({ task: t, role }) => {
              const due = dueOf(t, now);
              return (
                <RowMenu key={t.id} tasks={[t]} me={me} cans={cans} onPick={(v) => rowVerb(t.id, v)}>
                  {(more, menuOpen) => (
                    <li className="group/trow flex items-center gap-1.5">
                      {selecting ? (
                        <Check
                          aria-label={`Mark ${taskRef(t.id)}`}
                          checked={marked.has(t.id)}
                          onChange={() => undefined}
                          onClick={(e) => toggle(t.id, e.shiftKey)}
                        />
                      ) : null}
                      <button
                        className={`tasks-tab-row grid ${COLS} w-full min-w-0 cursor-pointer items-center gap-2 rounded-card border bg-panel px-2.5 py-1.5 text-left${due?.over ? ' overdue border-bad border-l-4' : ' border-line'}${CLOSED.includes(t.state) ? ' closed opacity-60' : ''}`}
                        data-task={t.id}
                        onClick={() => setOpen(t.id)}
                      >
                        <StateChip state={t.state} result={watchResult(t)} />
                        <span className="mono">{taskRef(t.id)}</span>
                        <span
                          className="ttitle min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-body font-medium"
                          title={t.title}
                        >
                          {t.title}
                        </span>
                        {role === 'assigned' ? (
                          <span className="who min-w-0 truncate text-hint text-ink-2" title={`Opened by ${agent}, held by ${t.to}`}>
                            <Icon name="agent" size={12} aria-hidden /> {bare(t.to)}
                          </span>
                        ) : t.from !== agent ? (
                          <span className="who min-w-0 truncate text-hint text-ink-2" title={`Opened by ${t.from}`}>
                            from {bare(t.from)}
                          </span>
                        ) : (
                          <span className="who" />
                        )}
                        {due ? (
                          <span
                            className={`due truncate text-right text-hint${due.over ? ' font-medium text-bad' : ' text-ink-2'}`}
                            data-over={due.over || undefined}
                          >
                            {due.over ? <Icon name="alert" size={12} aria-hidden /> : null} {due.text}
                          </span>
                        ) : (
                          <span className="due" />
                        )}
                      </button>
                      <span
                        className={`inline-flex ${menuOpen ? 'opacity-100' : 'opacity-0 group-hover/trow:opacity-100 focus-within:opacity-100 pointer-coarse:opacity-100'}`}
                      >
                        {more}
                      </span>
                    </li>
                  )}
                </RowMenu>
              );
            })}
          </ul>
        )}
        {more && view === 'list' ? (
          <div className="mt-2 flex items-center justify-center gap-3 text-hint text-ink-2">
            <span>{mine.length} shown · older ones exist</span>
            <Button
              variant="outline"
              size="sm"
              label={`Show ${TASK_PAGE} older`}
              aria-label={`Show ${TASK_PAGE} older`}
              disabled={loadingOlder}
              onClick={() => void loadOlder()}
            >
              {loadingOlder ? 'Loading…' : `Show ${TASK_PAGE} older`}
            </Button>
          </div>
        ) : null}
      </View>
      <VerbSheets flow={verbs} ids={[...marked]} />
      {selecting ? (
        <Toolbar label="Actions on the marked tasks">
          <span className="muted mr-auto">{marked.size} marked</span>
          <Button variant="outline" size="sm" label={marked.size >= mine.length ? 'Clear' : 'Select all'} onClick={toggleAll}>
            {marked.size >= mine.length ? 'Clear' : `Select all ${mine.length}`}
          </Button>
          {marked.size ? (
            <VerbMenu tasks={verbs.byId([...marked])} me={me} cans={cans} onPick={verbs.pick} title="Actions on the marked tasks" />
          ) : null}
        </Toolbar>
      ) : null}
    </section>
  );
}
