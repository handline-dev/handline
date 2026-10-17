// The Tasks page (docs/design/inbox-tasks-prompts.md §4): what the daemon lists, grouped by status, with filters, sort, ticks, the bridge's keys,
// and a detail beside it from 1100 px. Writes go through `useWrite`: an authored one shows at once with a hollow dot, a decided one shows "…".
import { Fragment, useEffect, useMemo, useRef, useState, type ComponentProps, type KeyboardEvent } from 'react';
import {
  CLOSED,
  defaultFilter,
  shown,
  doneBucket,
  groupTasks,
  sortTasks,
  taskAgents,
  type RowVerb,
  type TaskColumn,
  type TaskFilter,
  taskRef,
} from '@handline/app-core';
import type { Task } from '@handline/client';
import { useStore } from '@handline/app-react';
import { useAgents, useServes, useTasks } from '@handline/app-data';
import { goTasks } from './route.ts';
import { TaskDetail } from './TaskDetail.tsx';
import { TaskBands } from './TaskGraph.tsx';
import { NewTask } from './NewTask.tsx';
import { Row } from './TaskRows.tsx';
import { SORTS, TasksBar, TasksHead, type Scope } from './TasksHead.tsx';
import { KeysSheet, MergeSheet, OwnerSheet, PrioritySheet } from './TaskSheets.tsx';
import { Button, ScrollPage } from './ui/index.ts';
import { AnimatedList } from './AnimatedList.tsx';
import { useBulk } from './taskActions.ts';
import { ReasonSheet, useCan, useNow, useWide } from './taskui.tsx';
import { useMarks } from './useMarks.ts';
import { VerbMenu, VerbSheets, useVerbCans, useVerbFlow } from './TaskVerbUi.tsx';
import { View } from './View.tsx';

type Open = null | 'discard' | 'owner' | 'priority' | 'merge' | 'states' | 'keys' | 'new';

/** A task matches a search that is in its number, title, owner or asker. */
const matches = (query: string) => {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return (t: Task) => {
    const hay = `${taskRef(t.id)} ${t.title} ${t.to} ${t.from}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  };
};

const DAY = 86_400_000;

/** The pills: Closed keeps only the closed tasks, Mine the ones I own; Open and Mine list what closed in the last day (the graph picks its own). */
const inScope = (tasks: Task[], scope: Scope, me: string, now: number, graph: boolean): Task[] =>
  scope === 'closed'
    ? tasks.filter((t) => CLOSED.includes(t.state))
    : (scope === 'mine' ? tasks.filter((t) => t.to === me) : tasks).filter(
        (t) => graph || !CLOSED.includes(t.state) || now - Date.parse(t.done ?? t.created) <= DAY,
      );

const typing = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);

export function TasksPage({ route }: { route: string }) {
  const id = route && route !== 'graph' ? Number(route) : null;
  const wide = useWide(1100);
  if (route === 'graph') return <TaskList selected={null} graph />;
  if (id !== null && !wide) return <TaskDetail id={id} onBack={() => goTasks()} />;
  return (
    <div className={`tasks-split relative flex min-h-0 min-w-0 flex-1${id !== null ? ' with-pane' : ''}`}>
      <TaskList selected={id} />
      {id !== null ? <TaskDetail id={id} onBack={() => goTasks()} embedded drawer /> : null}
    </div>
  );
}

type RowProps = Omit<ComponentProps<typeof Row>, 'rows' | 't' | 'now' | 'markCell'>;

/** The table of tasks: a band of rows under each status heading. */
function TaskTable({
  groups,
  onFold,
  total,
  now,
  busy,
  markCell,
  rowProps,
}: {
  groups: (ReturnType<typeof groupTasks>[number] & { folded: boolean })[];
  onFold: () => void;
  total: number;
  now: number;
  busy: boolean;
  markCell: boolean;
  rowProps: (t: Task) => RowProps;
}) {
  return (
    <div className="tasks-scroll min-h-0 flex-1 overflow-auto max-sm:bg-transparent" aria-busy={busy}>
      <table className="ttable w-full table-fixed border-collapse max-sm:block" aria-label="Tasks">
        <colgroup className="max-sm:hidden">
          {['w-8', 'w-8', 'w-16', '', 'w-44', 'w-14', 'w-28'].map((w, i) => (
            <col key={i} className={w} />
          ))}
        </colgroup>
        <tbody className="max-sm:block">
          {groups.map((g) => (
            <Fragment key={g.label}>
              <tr className="tgroup max-sm:block" data-group={g.key}>
                <th
                  colSpan={7}
                  className="px-2 pt-4 pb-1 text-left text-label font-semibold data-[group=needs]:text-wait data-[group=running]:text-working"
                  data-group={g.key}
                >
                  {g.key === 'done' && g.label === 'Done today' ? (
                    <button type="button" className="border-0 bg-transparent p-0 font-semibold" aria-expanded={!g.folded} onClick={onFold}>
                      {g.label}{' '}
                      <span className="font-normal text-ink-3">
                        {g.rows.length} {g.folded ? '▸' : '▾'}
                      </span>
                    </button>
                  ) : (
                    <>
                      {g.label} <span className="font-normal text-ink-3">{g.rows.length}</span>
                    </>
                  )}
                </th>
              </tr>
              <AnimatedList>
                {(g.folded ? [] : g.rows).map((t) => (
                  <Row key={t.id} rows={total} t={t} now={now} markCell={markCell} {...rowProps(t)} />
                ))}
              </AnimatedList>
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TaskList({ selected, graph }: { selected: number | null; graph?: boolean }) {
  const now = useNow();
  const [filter, setFilter] = useState<TaskFilter>(defaultFilter);
  const [scope, setScope] = useState<Scope>('open');
  const me = useStore().me;
  const [sort, setSort] = useState<{ by: TaskColumn; reverse: boolean }>({ by: 'priority', reverse: false });
  const [cursor, setCursor] = useState<number | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [query, setQuery] = useState('');
  const root = useRef<HTMLElement>(null);

  const q = useTasks({ all: true }); // Open lists what closed today, the graph what closed lately
  const all = useMemo(() => q.data?.items ?? [], [q.data]);
  const rows = useMemo(
    () =>
      sortTasks(
        inScope(shown(all, { ...filter, closed: true }), scope, me, now, !!graph).filter(matches(query)),
        sort.by,
        sort.reverse,
        now,
      ),
    [all, filter, scope, graph, me, query, sort, now],
  );
  const [showDone, setShowDone] = useState(false);
  const groups = useMemo(
    () =>
      groupTasks(rows, me).flatMap((g) => {
        if (g.key !== 'done') return [{ ...g, folded: false }];
        if (scope === 'open') return [{ ...g, label: 'Done today', folded: !showDone }];
        // Closed: by the day it closed, newest first
        return ['Today', 'Yesterday', 'This week', 'Earlier']
          .map((label) => ({ ...g, label, rows: g.rows.filter((t) => doneBucket(t, now) === label), folded: false }))
          .filter((b) => b.rows.length);
      }),
    [rows, me, scope, showDone, now],
  );
  const order = useMemo(() => groups.flatMap((g) => (g.folded ? [] : g.rows)), [groups]);
  const { marked, setMarked, toggle, toggleAll } = useMarks(order.map((t) => t.id));
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];

  const bulk = useBulk(() => setMarked(new Set()));
  const canEdit = useCan('task.edit');
  const canDiscard = useCan('task.discard');
  const canMerge = useCan('task.merge');
  const cans = useVerbCans();
  const listServed = useServes('tasks.list').served;

  useEffect(() => {
    if (cursor === null || !order.some((t) => t.id === cursor)) setCursor(order[0]?.id ?? null);
  }, [order, cursor]);

  // A task opened from elsewhere (My turn, a link) is brought into view in the list.
  const listed = order.length > 0;
  useEffect(() => {
    if (selected !== null && listed) root.current?.querySelector(`[data-task="${selected}"]`)?.scrollIntoView?.({ block: 'nearest' });
  }, [selected, listed]);

  /** Closes the sheet and gives the keys back to the page, so the next key works without a click. */
  const shut = () => {
    setOpen(null);
    queueMicrotask(() => root.current?.focus());
  };
  const targets = (): number[] => (marked.size ? order.filter((t) => marked.has(t.id)).map((t) => t.id) : cursor === null ? [] : [cursor]);
  /** Owner, priority and discard: the sheet closes and the batch runs on the marked rows (or the cursor row). */
  const own = (verb: RowVerb, arg: string) => {
    shut();
    void bulk.run(verb, targets(), arg);
  };
  const doMerge = () => {
    const into = cursor;
    const tasks = [...marked].filter((t) => t !== into);
    shut();
    if (into !== null && tasks.length) void bulk.merge(into, tasks);
  };

  const nextSort = (reverse: boolean) => setSort((s) => ({ by: SORTS[(SORTS.indexOf(s.by) + 1) % SORTS.length]!, reverse }));
  const nextAgent = () => {
    const names = taskAgents(all);
    setFilter((f) => ({ ...f, agent: names[(names.indexOf(f.agent) + 1) % (names.length + 1)] ?? '' }));
  };
  const move = (d: number) => {
    const i = order.findIndex((t) => t.id === cursor);
    const next = order[Math.min(order.length - 1, Math.max(0, i + d))];
    if (next) setCursor(next.id);
  };
  const verbs = useVerbFlow({ all, me, agents, bulk, focus: () => root.current?.focus(), own: (v) => act(v) });
  const act = (what: Exclude<Open, null>) => {
    if (what === 'discard' && !canDiscard.ok) return;
    if ((what === 'owner' || what === 'priority') && !canEdit.ok) return;
    if (what === 'merge' && (!canMerge.ok || ![...marked].some((t) => t !== cursor))) return; // folds the marked into the cursor row
    if (what !== 'new' && what !== 'keys' && what !== 'states' && !targets().length) return;
    setOpen(what);
  };

  function onKey(e: KeyboardEvent) {
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    if (graph && !'?nafv'.includes(k)) return; // the graph draws no rows, cursor or ticks for the other keys to act on
    const keys: Record<string, () => void> = {
      ' ': () => cursor !== null && toggle(cursor),
      A: toggleAll,
      Enter: () => cursor !== null && goTasks(cursor),
      d: () => act('discard'),
      o: () => act('owner'),
      p: () => act('priority'),
      m: () => act('merge'),
      s: () => nextSort(false),
      S: () => nextSort(true),
      a: () => setScope((s) => (s === 'closed' ? 'open' : 'closed')),
      v: () => act('states'),
      f: nextAgent,
      n: () => act('new'),
      '?': () => act('keys'),
      j: () => move(1),
      ArrowDown: () => move(1),
      k: () => move(-1),
      ArrowUp: () => move(-1),
      Escape: () => (open ? shut() : setMarked(new Set())),
    };
    const f = keys[k];
    if (!f) return;
    e.preventDefault();
    f();
  }

  const listBody = (
    <>
      {graph ? (
        <TaskBands tasks={rows} now={now} recent={scope !== 'closed'} />
      ) : (
        <TaskTable
          groups={groups}
          onFold={() => setShowDone(!showDone)}
          total={order.length}
          now={now}
          busy={q.isFetching && !q.data}
          markCell={marked.size > 0}
          rowProps={rowProps}
        />
      )}
      {open === 'discard' ? (
        <ReasonSheet
          label={targets().some((id) => all.find((t) => t.id === id)?.state === 'doing') ? 'stop it and discard - why:' : 'discard - why:'}
          submit="Discard"
          onCancel={shut}
          onSubmit={(reason) => own('discard', reason)}
        />
      ) : null}
      {open === 'owner' ? <OwnerSheet agents={agents} onCancel={shut} onPick={(o) => own('owner', o)} /> : null}
      {open === 'priority' ? <PrioritySheet onCancel={shut} onPick={(p) => own('priority', String(p))} /> : null}
      {open === 'merge' ? (
        <MergeSheet count={[...marked].filter((t) => t !== cursor).length} into={cursor} onCancel={shut} onSubmit={doMerge} />
      ) : null}
      {open === 'keys' ? <KeysSheet onClose={shut} /> : null}
      <VerbSheets flow={verbs} ids={targets()} />
      {marked.size ? (
        <TasksBar marked={marked.size} can={{ edit: canEdit, merge: canMerge }} onAct={act} onClear={() => setMarked(new Set())}>
          <VerbMenu tasks={verbs.byId([...marked])} me={me} cans={cans} onPick={verbs.pick} title="More actions" word="More ▾" />
        </TasksBar>
      ) : null}
    </>
  );

  function rowProps(t: Task) {
    return {
      marked: marked.has(t.id),
      cursor: cursor === t.id,
      open: selected === t.id,
      mark: (t.id < 0 || bulk.authored.has(t.id) ? 'authored' : bulk.decided.has(t.id) ? 'decided' : null) as 'authored' | 'decided' | null,
      failed: bulk.failed.has(t.id),
      onCursor: () => setCursor(t.id),
      onMark: (range: boolean) => toggle(t.id, range),
    };
  }

  // The daemon is asked for closed tasks only when they are shown, so "nothing at all" can only be said once they are.
  const none = scope === 'closed' && all.length === 0;
  return (
    <ScrollPage
      beside={selected !== null}
      className="tasks flex flex-col pt-4 outline-none"
      ref={root}
      tabIndex={0}
      aria-label="Tasks"
      onKeyDown={onKey}
    >
      <TasksHead
        agents={taskAgents(all)}
        filter={filter}
        scope={scope}
        onScope={setScope}
        filtering={open === 'states'}
        view={graph ? 'graph' : 'list'}
        query={query}
        onQuery={setQuery}
        onFilter={setFilter}
        onView={(v) => goTasks(v === 'graph' ? 'graph' : undefined)}
        onAct={(what) => (what === 'states' && open === 'states' ? shut() : act(what))}
      />
      {open === 'new' ? <NewTask agents={agents} onClose={shut} /> : null}
      <View q={q} served={listServed} shape="tasks" what="tasks">
        {rows.length === 0 ? (
          <div className="empty">
            {none ? (
              <>
                <p>Nothing has been asked yet.</p>
                <Button variant="solid" label="New task" onClick={() => act('new')} />
              </>
            ) : (
              <>
                <p>No tasks in these states.</p>
                {scope !== 'closed' ? <Button variant="outline" size="sm" label="Show closed" onClick={() => setScope('closed')} /> : null}
              </>
            )}
          </div>
        ) : (
          listBody
        )}
      </View>
    </ScrollPage>
  );
}
