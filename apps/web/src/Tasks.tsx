// The Tasks page (docs/design/inbox-tasks-prompts.md §4): the table or the tree of what the daemon lists, filters, sort, marks, the bridge's keys,
// and a detail beside it from 1100 px. Writes go through `useWrite`: an authored one shows at once with a hollow dot, a decided one shows "…".
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { defaultFilter, shown, sortTasks, taskAgents, taskTree, type KV, type TaskColumn, type TaskFilter } from '@handline/app-core';
import type { Task } from '@handline/client';
import { useAgents, useServes, useTasks } from '@handline/app-data';
import { setting } from './daemon.ts';
import { goTasks } from './route.ts';
import { TaskDetail } from './TaskDetail.tsx';
import { NewTask } from './NewTask.tsx';
import { Row } from './TaskRows.tsx';
import { HEADS, SORTS, TasksHead } from './TasksHead.tsx';
import { KeysSheet, OwnerSheet, PrioritySheet, StatesSheet } from './TaskSheets.tsx';
import { useBulk } from './taskActions.ts';
import { ReasonSheet, Sheet, useCan, useNow, useWide } from './taskui.tsx';
import { View } from './View.tsx';

type Mode = 'list' | 'tree';
type Open = null | 'discard' | 'owner' | 'priority' | 'merge' | 'states' | 'keys' | 'new';

const typing = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);

export function TasksPage({ storage, route }: { storage: KV | null; route: string }) {
  const id = route ? Number(route) : null;
  const wide = useWide(1100);
  if (id !== null && !wide) return <TaskDetail id={id} onBack={() => goTasks()} />;
  return (
    <div className={`tasks-split${id !== null ? ' with-pane' : ''}`}>
      <TaskList storage={storage} selected={id} />
      {id !== null ? <TaskDetail id={id} onBack={() => goTasks()} embedded /> : null}
    </div>
  );
}

function TaskList({ storage, selected }: { storage: KV | null; selected: number | null }) {
  const now = useNow();
  const [mode, setMode] = useState<Mode>(() => (setting(storage, 'tasksView', 'list').value === 'tree' ? 'tree' : 'list'));
  const [filter, setFilter] = useState<TaskFilter>(defaultFilter);
  const [sort, setSort] = useState<{ by: TaskColumn; reverse: boolean }>({ by: 'priority', reverse: false });
  const [marked, setMarked] = useState<ReadonlySet<number>>(new Set());
  const [cursor, setCursor] = useState<number | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const [selecting, setSelecting] = useState(false);
  const root = useRef<HTMLElement>(null);

  const q = useTasks(filter.closed ? { all: true } : {});
  const all = useMemo(() => q.data?.items ?? [], [q.data]);
  const rows = useMemo(() => sortTasks(shown(all, filter), sort.by, sort.reverse, now), [all, filter, sort, now]);
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
      a: () => setFilter((f) => ({ ...f, closed: !f.closed })),
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
      <div className="tasks-scroll" aria-busy={q.isFetching && !q.data}>
        <table className={`ttable${selecting ? ' selecting' : ''}`} aria-label="Tasks">
          <thead>
            <tr>
              <th className="c-mark">
                <input
                  type="checkbox"
                  aria-label="Mark every row shown"
                  checked={!!order.length && marked.size === order.length}
                  onChange={toggleAll}
                />
              </th>
              {mode === 'tree' ? (
                <>
                  <th className="c-title">task</th>
                  <th className="c-owner">owner</th>
                  <th className="c-state">state</th>
                  <th className="c-under">under</th>
                </>
              ) : (
                HEADS.map((h) => (
                  <th key={h.col} className={h.cls} aria-sort={sort.by === h.col ? (sort.reverse ? 'descending' : 'ascending') : undefined}>
                    <button className="head" onClick={() => setSort((s) => ({ by: h.col, reverse: s.by === h.col && !s.reverse }))}>
                      {h.label}
                      {sort.by === h.col ? (sort.reverse ? ' ▾' : ' ▴') : ''}
                    </button>
                  </th>
                ))
              )}
            </tr>
          </thead>
          <tbody>
            {mode === 'tree'
              ? tree.map(({ task: t, depth, dim }) => <Row key={t.id} t={t} now={now} tree depth={depth} dim={dim} {...rowProps(t)} />)
              : rows.map((t) => <Row key={t.id} t={t} now={now} {...rowProps(t)} />)}
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
          label={`fold ${[...marked].filter((t) => t !== cursor).length} into #${cursor}?`}
          submit="Fold"
          onCancel={shut}
          onSubmit={doMerge}
        >
          <span className="muted">The folded tasks close; #{cursor} keeps going.</span>
        </Sheet>
      ) : null}
      {open === 'states' ? <StatesSheet filter={filter} onChange={setFilter} onClose={shut} /> : null}
      {open === 'keys' ? <KeysSheet onClose={shut} /> : null}
      <div className="tbar" role="toolbar" aria-label="Actions on the marked tasks">
        <span className="muted">{marked.size ? `${marked.size} marked` : 'acts on the row under the cursor'}</span>
        <button className="btn" onClick={() => act('discard')} disabled={!canDiscard.ok} title={canDiscard.why}>
          Discard <kbd>d</kbd>
        </button>
        <button className="btn" onClick={() => act('owner')} disabled={!canEdit.ok} title={canEdit.why}>
          Owner <kbd>o</kbd>
        </button>
        <button className="btn" onClick={() => act('priority')} disabled={!canEdit.ok} title={canEdit.why}>
          Priority <kbd>p</kbd>
        </button>
        <button
          className="btn"
          onClick={() => act('merge')}
          disabled={!canMerge.ok}
          title={canMerge.why ?? 'mark tasks, put the cursor on the one to keep'}
        >
          Merge <kbd>m</kbd>
        </button>
      </div>
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
      onMark: () => toggle(t.id),
    };
  }

  // The daemon is asked for closed tasks only when they are shown, so "nothing at all" can only be said once they are.
  const none = filter.closed && all.length === 0;
  return (
    <section className="tasks" ref={root} tabIndex={0} aria-label="Tasks" onKeyDown={onKey}>
      <TasksHead
        agents={taskAgents(all)}
        filter={filter}
        mode={mode}
        sort={sort.by}
        selecting={selecting}
        onFilter={setFilter}
        onView={setView}
        onSort={(by) => setSort({ by, reverse: false })}
        onSelecting={() => setSelecting(!selecting)}
        onAct={act}
      />
      {open === 'new' ? <NewTask agents={agents} onClose={shut} /> : null}
      <View q={q} served={listServed} shape="tasks" what="tasks">
        {rows.length === 0 && !(mode === 'tree' && tree.length) ? (
          <div className="empty">
            {none ? (
              <>
                <p>Nothing has been asked yet.</p>
                <button className="btn primary" onClick={() => act('new')}>
                  New task
                </button>
              </>
            ) : (
              <>
                <p>No tasks in these states.</p>
                {!filter.closed ? (
                  <button className="btn" onClick={() => setFilter({ ...filter, closed: true })}>
                    Show closed
                  </button>
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
