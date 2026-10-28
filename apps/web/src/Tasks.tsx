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
  taskMatches,
  type RowVerb,
  type TaskColumn,
  type TaskFilter,
} from '@handline/app-core';
import type { Task } from '@handline/client';
import { useMe } from '@handline/app-react';
import { TASK_PAGE, useAgents, useServes, usePagedTasks } from '@handline/app-data';
import { goTasks } from '#src/route.ts';
import { TaskDetail } from '#src/TaskDetail.tsx';
import { TaskColumns } from '#src/TaskGraph.tsx';
import { NewTask } from '#src/NewTask.tsx';
import { Row } from '#src/TaskRows.tsx';
import { SORTS, TasksBar, TasksHead, type Scope } from '#src/TasksHead.tsx';
import { KeysSheet, MergeSheet, OwnerSheet, PrioritySheet } from '#src/TaskSheets.tsx';
import { Button, ScrollPage } from '#src/ui/index.ts';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { useBulk } from '#src/taskActions.ts';
import { ReasonSheet, useCan, useNow, useTableColumns, useWide } from '#src/taskui.tsx';
import { useMarks } from '#src/useMarks.ts';
import { VerbMenu, VerbSheets, useVerbCans, useVerbFlow } from '#src/TaskVerbUi.tsx';
import { View } from '#src/View.tsx';
import { typing } from '#src/typing.ts';

type Open = null | 'discard' | 'owner' | 'priority' | 'merge' | 'states' | 'keys' | 'new';

const DAY = 86_400_000;

/** The pills: Closed keeps only the closed tasks, Mine the ones I own; Open and Mine list what closed in the last day (the graph picks its own). */
const inScope = (tasks: Task[], scope: Scope, me: string, now: number, graph: boolean): Task[] =>
  scope === 'closed'
    ? tasks.filter((t) => CLOSED.includes(t.state))
    : (scope === 'mine' ? tasks.filter((t) => t.to === me) : tasks).filter(
        (t) => graph || !CLOSED.includes(t.state) || now - Date.parse(t.done ?? t.created) <= DAY,
      );

export function TasksPage({ route }: { route: string }) {
  const id = route && route !== 'graph' ? Number(route) : null;
  const wide = useWide(1100);
  if (route === 'graph') return <TaskList selected={null} graph />;
  if (id !== null && !wide) return <TaskDetail id={id} />;
  return (
    <div className={`tasks-split relative flex min-h-0 min-w-0 flex-1${id !== null ? ' with-pane' : ''}`}>
      <TaskList selected={id} />
      {id !== null ? <TaskDetail id={id} embedded drawer /> : null}
    </div>
  );
}

type RowProps = Omit<ComponentProps<typeof Row>, 'rows' | 't' | 'now' | 'markCell' | 'columns'>;

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
  const scroll = useRef<HTMLDivElement>(null);
  const columns = useTableColumns(scroll);
  const widths = ['w-8', 'w-8', 'w-16', '', ...(columns.owner ? ['w-28'] : []), ...(columns.pri ? ['w-14'] : []), 'w-36'];
  return (
    <div ref={scroll} className="tasks-scroll min-h-0 flex-1 overflow-auto max-sm:bg-transparent" aria-busy={busy}>
      <table className="ttable w-full table-fixed border-collapse max-sm:block" aria-label="Tasks">
        <colgroup className="max-sm:hidden">
          {widths.map((w, i) => (
            <col key={i} className={w} />
          ))}
        </colgroup>
        <tbody className="max-sm:block">
          {groups.map((g) => (
            <Fragment key={g.label}>
              <tr className="tgroup max-sm:block" data-group={g.key}>
                <th
                  colSpan={widths.length}
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
                  <Row key={t.id} columns={columns} rows={total} t={t} now={now} markCell={markCell} {...rowProps(t)} />
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
  const me = useMe();
  const [sort, setSort] = useState<{ by: TaskColumn; reverse: boolean }>({ by: 'priority', reverse: false });
  const [cursor, setCursor] = useState<number | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [query, setQuery] = useState('');
  const root = useRef<HTMLElement>(null);

  const { query: q, items: all, more, loadOlder, loadingOlder } = usePagedTasks({ all: true }); // Open lists what closed today, the graph what closed lately
  const rows = useMemo(
    () =>
      sortTasks(
        inScope(shown(all, { ...filter, closed: true }), scope, me, now, !!graph).filter(taskMatches(query)),
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
    // One task is discarded at once; several are confirmed with their count first.
    if (what === 'discard' && targets().length === 1) return own('discard', '');
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
        <TaskColumns tasks={rows} now={now} me={me} scopeKey={scope} />
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
      <AnimatedList>
        {open === 'discard' ? (
          <ReasonSheet
            key="discard"
            label={
              targets().some((id) => all.find((t) => t.id === id)?.state === 'doing')
                ? 'stop it and discard - why (optional):'
                : 'discard - why (optional):'
            }
            submit="Discard"
            optional
            onCancel={shut}
            onSubmit={(reason) => own('discard', reason)}
          />
        ) : null}
      </AnimatedList>
      <AnimatedList>
        {open === 'owner' ? <OwnerSheet key="OwnerSheet" agents={agents} onCancel={shut} onPick={(o) => own('owner', o)} /> : null}
      </AnimatedList>
      <AnimatedList>
        {open === 'priority' ? <PrioritySheet key="PrioritySheet" onCancel={shut} onPick={(p) => own('priority', String(p))} /> : null}
      </AnimatedList>
      <AnimatedList>
        {open === 'merge' ? (
          <MergeSheet key="merge" count={[...marked].filter((t) => t !== cursor).length} into={cursor} onCancel={shut} onSubmit={doMerge} />
        ) : null}
      </AnimatedList>
      <AnimatedList>{open === 'keys' ? <KeysSheet key="KeysSheet" onClose={shut} /> : null}</AnimatedList>
      <VerbSheets flow={verbs} ids={targets()} />
      {marked.size ? (
        <TasksBar marked={marked.size} can={{ edit: canEdit, merge: canMerge }} onAct={act} onClear={() => setMarked(new Set())}>
          <VerbMenu tasks={verbs.byId([...marked])} me={me} cans={cans} onPick={verbs.pick} title="More actions" word="More ▾" />
        </TasksBar>
      ) : null}
    </>
  );

  /** The row's menu acts on that row, or on the marked rows when it is one of them. */
  function rowVerb(id: number, verb: RowVerb) {
    if (!marked.has(id)) setMarked(new Set([id]));
    verbs.pick(verb);
  }

  function rowProps(t: Task) {
    return {
      marked: marked.has(t.id),
      cursor: cursor === t.id,
      open: selected === t.id,
      mark: (t.id < 0 || bulk.authored.has(t.id) ? 'authored' : bulk.decided.has(t.id) ? 'decided' : null) as 'authored' | 'decided' | null,
      failed: bulk.failed.has(t.id),
      verbs: { cans, onPick: (v: RowVerb) => rowVerb(t.id, v) },
      onCursor: () => setCursor(t.id),
      onMark: (range: boolean) => toggle(t.id, range),
    };
  }

  // The daemon is asked for closed tasks only when they are shown, so "nothing at all" can only be said once they are.
  const none = scope === 'closed' && all.length === 0;
  return (
    <ScrollPage
      beside={selected !== null}
      className="tasks flex flex-col outline-none"
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
      <AnimatedList>{open === 'new' ? <NewTask key="NewTask" agents={agents} onClose={shut} /> : null}</AnimatedList>
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
      {more ? (
        <div className="flex items-center justify-center gap-3 py-3 text-hint text-ink-2">
          <span>{all.length} loaded · older ones exist</span>
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
    </ScrollPage>
  );
}
