// The Tasks page (docs/design/inbox-tasks-prompts.md §4): the table or the tree of what the daemon lists, filters, sort, marks, the bridge's keys,
// and a detail beside it from 1100 px. Writes go through `useWrite`: an authored one shows at once with a hollow dot, a decided one shows "…".
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import {
  CLOSED,
  defaultFilter,
  shown,
  sortTasks,
  taskAgents,
  taskTree,
  type KV,
  type TaskColumn,
  type TaskFilter,
} from '@handline/app-core';
import type { Task } from '@handline/client';
import { useStore } from '@handline/app-react';
import { useAgents, useServes, useTasks } from '@handline/app-data';
import { setting } from './daemon.ts';
import { goTasks } from './route.ts';
import { TaskDetail } from './TaskDetail.tsx';
import { NewTask } from './NewTask.tsx';
import { Row } from './TaskRows.tsx';
import { SORTS, TasksBar, TasksHead, type Scope } from './TasksHead.tsx';
import { TableHead } from './TaskTableHead.tsx';
import { KeysSheet, OwnerSheet, PrioritySheet } from './TaskSheets.tsx';
import { Button } from './ui/index.ts';
import { AnimatedList } from './AnimatedList.tsx';
import { useBulk } from './taskActions.ts';
import { ReasonSheet, Sheet, useCan, useNow, useWide } from './taskui.tsx';
import { View } from './View.tsx';

type Mode = 'list' | 'tree';
type Open = null | 'discard' | 'owner' | 'priority' | 'merge' | 'states' | 'keys' | 'new';

/** A task matches a search that is in its number, title, owner or asker. */
const matches = (query: string) => {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return (t: Task) => {
    const hay = `T${t.id} ${t.title} ${t.to} ${t.from}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  };
};

/** The pills: Closed keeps only the closed tasks, Mine the ones I own. */
const inScope = (tasks: Task[], scope: Scope, me: string): Task[] =>
  scope === 'closed' ? tasks.filter((t) => CLOSED.includes(t.state)) : scope === 'mine' ? tasks.filter((t) => t.to === me) : tasks;

const typing = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);

export function TasksPage({ storage, route }: { storage: KV | null; route: string }) {
  const id = route ? Number(route) : null;
  const wide = useWide(1100);
  if (id !== null && !wide) return <TaskDetail id={id} onBack={() => goTasks()} />;
  return (
    <div className={`tasks-split relative flex min-h-0 min-w-0 flex-1${id !== null ? ' with-pane' : ''}`}>
      <TaskList storage={storage} selected={id} />
      {id !== null ? <TaskDetail id={id} onBack={() => goTasks()} embedded drawer /> : null}
    </div>
  );
}

function TaskList({ storage, selected }: { storage: KV | null; selected: number | null }) {
  const now = useNow();
  const [mode, setMode] = useState<Mode>(() => (setting(storage, 'tasksView', 'list').value === 'tree' ? 'tree' : 'list'));
  const [filter, setFilter] = useState<TaskFilter>(defaultFilter);
  const [scope, setScope] = useState<Scope>('open');
  const me = useStore().me;
  const [sort, setSort] = useState<{ by: TaskColumn; reverse: boolean }>({ by: 'priority', reverse: false });
  const [marked, setMarked] = useState<ReadonlySet<number>>(new Set());
  const [cursor, setCursor] = useState<number | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [query, setQuery] = useState('');
  const [selecting, setSelecting] = useState(false);
  const root = useRef<HTMLElement>(null);

  const q = useTasks(scope === 'closed' ? { all: true } : {});
  const all = useMemo(() => q.data?.items ?? [], [q.data]);
  const rows = useMemo(
    () =>
      sortTasks(
        inScope(shown(all, { ...filter, closed: scope === 'closed' }), scope, me).filter(matches(query)),
        sort.by,
        sort.reverse,
        now,
      ),
    [all, filter, scope, me, query, sort, now],
  );
  const tree = useMemo(() => (mode === 'tree' ? taskTree(rows, all) : []), [mode, rows, all]);
  const order = mode === 'tree' ? tree.map((r) => r.task) : rows;
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];

  const bulk = useBulk(() => setMarked(new Set()));
  const canEdit = useCan('task.edit');
  const canDiscard = useCan('task.discard');
  const canMerge = useCan('task.merge');
  const listServed = useServes('tasks.list').served;

  useEffect(() => {
    if (cursor === null || !order.some((t) => t.id === cursor)) setCursor(order[0]?.id ?? null);
  }, [order, cursor]);

  /** Closes the sheet and gives the keys back to the page, so the next key works without a click. */
  const shut = () => {
    setOpen(null);
    queueMicrotask(() => root.current?.focus());
  };
  const targets = (): number[] => (marked.size ? order.filter((t) => marked.has(t.id)).map((t) => t.id) : cursor === null ? [] : [cursor]);
  const setOwner = (owner: string) => {
    shut();
    void bulk.owner(targets(), owner);
  };
  const setPriority = (priority: 1 | 2 | 3) => {
    shut();
    void bulk.priority(targets(), priority);
  };
  const doDiscard = (reason: string) => {
    shut();
    void bulk.discard(targets(), reason);
  };
  const doMerge = () => {
    const into = cursor;
    const tasks = [...marked].filter((t) => t !== into);
    shut();
    if (into !== null && tasks.length) void bulk.merge(into, tasks);
  };

  const setView = (m: Mode) => {
    setMode(m);
    setting(storage, 'tasksView', 'list').save(m);
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
  const toggle = (id: number) =>
    setMarked((m) => {
      const n = new Set(m);
      if (!n.delete(id)) n.add(id);
      return n;
    });
  const toggleAll = () => setMarked((m) => (m.size === order.length ? new Set() : new Set(order.map((t) => t.id))));
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
      t: () => setView(mode === 'tree' ? 'list' : 'tree'),
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
      <div className="tasks-scroll min-h-0 flex-1 overflow-auto rounded-card border border-line bg-surface max-sm:bg-transparent max-sm:border-0" aria-busy={q.isFetching && !q.data}>
        <table className={`ttable w-full table-fixed border-collapse max-sm:block${selecting || marked.size ? ' selecting' : ''}`} aria-label="Tasks">
          <TableHead
            tree={mode === 'tree'}
            sort={sort}
            allMarked={!!order.length && marked.size === order.length}
            markCell={selecting || marked.size > 0}
            onToggleAll={toggleAll}
            onSort={(col) => setSort((s) => ({ by: col, reverse: s.by === col && !s.reverse }))}
          />
          <tbody className="max-sm:block">
            <AnimatedList>
              {mode === 'tree'
                ? tree.map(({ task: t, depth, dim }) => (
                    <Row key={t.id} rows={tree.length} t={t} now={now} tree depth={depth} dim={dim} markCell={selecting || marked.size > 0} {...rowProps(t)} />
                  ))
                : rows.map((t) => <Row key={t.id} rows={rows.length} t={t} now={now} markCell={selecting || marked.size > 0} {...rowProps(t)} />)}
            </AnimatedList>
          </tbody>
        </table>
      </div>
      {open === 'discard' ? (
        <ReasonSheet
          label={targets().some((id) => all.find((t) => t.id === id)?.state === 'doing') ? 'stop it and discard - why:' : 'discard - why:'}
          submit="Discard"
          onCancel={shut}
          onSubmit={doDiscard}
        />
      ) : null}
      {open === 'owner' ? <OwnerSheet agents={agents} onCancel={shut} onPick={setOwner} /> : null}
      {open === 'priority' ? <PrioritySheet onCancel={shut} onPick={setPriority} /> : null}
      {open === 'merge' ? (
        <Sheet
          label={`fold ${[...marked].filter((t) => t !== cursor).length} into T${cursor}?`}
          submit="Fold"
          onCancel={shut}
          onSubmit={doMerge}
        >
          <span className="muted">The folded tasks close; T{cursor} keeps going.</span>
        </Sheet>
      ) : null}
      {open === 'keys' ? <KeysSheet onClose={shut} /> : null}
      {marked.size ? <TasksBar marked={marked.size} can={{ discard: canDiscard, edit: canEdit, merge: canMerge }} onAct={act} /> : null}
    </>
  );

  /** A row's own icon: the cursor goes to it, and the marked rows stay the target only when it is one of them. */
  function rowAct(id: number, what: 'discard' | 'owner' | 'priority') {
    if (what === 'discard' ? !canDiscard.ok : !canEdit.ok) return;
    setCursor(id);
    if (!marked.has(id)) setMarked(new Set());
    setOpen(what);
  }

  function rowProps(t: Task) {
    return {
      marked: marked.has(t.id),
      cursor: cursor === t.id,
      open: selected === t.id,
      mark: (t.id < 0 || bulk.authored.has(t.id) ? 'authored' : bulk.decided.has(t.id) ? 'decided' : null) as 'authored' | 'decided' | null,
      failed: bulk.failed.has(t.id),
      onCursor: () => setCursor(t.id),
      onMark: () => toggle(t.id),
      can: { discard: canDiscard, edit: canEdit },
      onAct: (what: 'discard' | 'owner' | 'priority') => rowAct(t.id, what),
    };
  }

  // The daemon is asked for closed tasks only when they are shown, so "nothing at all" can only be said once they are.
  const none = scope === 'closed' && all.length === 0;
  return (
    <section
      className="tasks flex min-h-0 min-w-0 flex-1 flex-col px-4 py-3 outline-none max-sm:px-4 max-sm:py-3"
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
        mode={mode}
        selecting={selecting}
        query={query}
        onQuery={setQuery}
        onFilter={setFilter}
        onView={setView}
        onSelecting={() => setSelecting(!selecting)}
        onAct={(what) => (what === 'states' && open === 'states' ? shut() : act(what))}
      />
      {open === 'new' ? <NewTask agents={agents} onClose={shut} /> : null}
      <View q={q} served={listServed} shape="tasks" what="tasks">
        {rows.length === 0 && !(mode === 'tree' && tree.length) ? (
          <div className="empty">
            {none ? (
              <>
                <p>Nothing has been asked yet.</p>
                <Button variant="solid" label="New task" onClick={() => act('new')} />
              </>
            ) : (
              <>
                <p>No tasks in these states.</p>
                {scope !== 'closed' ? (
                  <Button variant="outline" size="sm" label="Show closed" onClick={() => setScope('closed')} />
                ) : null}
              </>
            )}
          </div>
        ) : (
          listBody
        )}
      </View>
    </section>
  );
}
