// One task: title and status, why it waits, owner, priority and time box as chips, the brief (folded), what it waits on and what hangs under it,
// one Activity list, and along the bottom the one main action, Cancel and More. The history opens from More. A verb that does not apply is
// hidden; one the daemon does not serve yet is shown disabled.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { dateTime, HELD, taskRef, watchResult } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, usePending, useTask, useTaskHistory, useWrite } from '@handline/app-data';
import { Button, MenuButton, MenuItem, MenuSeparator } from '#src/ui/index.ts';
import { goTasks } from '#src/route.ts';
import { ReasonSheet, Sheet, StateChip, useCan, useNow } from '#src/taskui.tsx';
import { AssignSheet } from '#src/TaskPeople.tsx';
import { CancelSheet, TaskChips, TaskWatch, TaskLinks } from '#src/TaskDetailParts.tsx';
import { AgentLink, BodyInline, Inline, TextInput, useEditTask } from '#src/TaskInline.tsx';
import { TaskHistory } from '#src/TaskHistory.tsx';
import { Activity } from '#src/TaskActivity.tsx';
import { Markdown } from '#src/Markdown.tsx';
import { RichText } from '#src/RichText.tsx';
import { View } from '#src/View.tsx';

type Ask = null | 'done' | 'reopen' | 'discard' | 'cancel' | 'withdraw' | 'release' | 'assign';

const CLOSED = ['done', 'discarded'];
const typing = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
/** The bridge's own sentence for a key that does not apply. */
const REOPEN_ONLY = 'only a done task that you asked for can be reopened';
const H3 = 'mt-4 mb-1 flex items-center text-ink-3';

export function TaskDetail({
  id,
  onBack,
  embedded,
  drawer,
}: {
  id: number;
  onBack: () => void;
  /** A pane inside its host (the Tasks tab of an agent pane, the Tasks page's split). */
  embedded?: boolean;
  /** The drawer over the Tasks table: absolute under the toolbar, only the Tasks page asks for it. */
  drawer?: boolean;
}) {
  const q = useTask(id);
  const gone = q.isError && classify(q.error).kind === 'not_found';
  return (
    <article
      className={`tdetail flex min-h-0 min-w-0 flex-1 flex-col overflow-auto px-4 py-3 outline-none max-sm:px-4 max-sm:py-3${
        embedded ? ' pane relative bg-panel -outline-offset-1' : ' mx-auto w-full max-w-[var(--w-page)]'
      }${
        drawer
          ? ' absolute top-[calc(var(--sp-5)+var(--h-btn)+var(--sp-4))] right-0 bottom-0 left-auto z-[5] w-[min(480px,100%)] border-l border-line bg-surface shadow-[var(--shadow-pop)]'
          : ''
      }`}
      aria-label={`Task ${taskRef(id)}`}
    >
      <div className="mb-4 flex items-center justify-end gap-2">
        <Button icon="close" label="Back" title="Back (Esc)" className="back" onClick={onBack}>
          Back
        </Button>
      </div>
      {gone ? (
        <div className="empty">
          <p>This task no longer exists.</p>
        </div>
      ) : (
        <View q={q} served shape="taskDetail" what={`task ${taskRef(id)}`}>
          {q.data ? <Body t={q.data} /> : null}
        </View>
      )}
    </article>
  );
}

function Body({ t }: { t: Detail }) {
  const store = useStore();
  const now = useNow();
  const me = store.me;
  const [ask, setAsk] = useState<Ask>(null);
  const commentBox = useRef<HTMLDivElement>(null);
  const edit = useEditTask(t);
  const [showHistory, setShowHistory] = useState(false);
  const done = useWrite('task.done');
  const queue = useWrite('task.queue');
  const draft = useWrite('task.draft');
  const reopen = useWrite('task.reopen');
  const discard = useWrite('task.discard');
  const withdraw = useWrite('task.withdraw');
  const cancel = useWrite('task.cancel');
  const release = useWrite('task.release');
  const can = {
    done: useCan('task.done'),
    queue: useCan('task.queue'),
    draft: useCan('task.draft'),
    reopen: useCan('task.reopen'),
    discard: useCan('task.discard'),
    withdraw: useCan('task.withdraw'),
    cancel: useCan('task.cancel'),
    release: useCan('task.release'),
  };
  const authored = [...usePending('task.edit'), ...usePending('task.done'), ...usePending('task.queue'), ...usePending('task.draft')].some(
    (v) => ('task' in v ? v.task === t.id : v.tasks.includes(t.id)),
  );
  const decided = [
    ...usePending('task.withdraw'),
    ...usePending('task.cancel'),
    ...usePending('task.discard'),
    ...usePending('task.reopen'),
  ].some((v) => v.task === t.id);
  const open = !CLOSED.includes(t.state);
  const mine = t.from === me;
  const running = t.state === 'doing';
  const startable = t.state === 'draft' || t.state === 'queued' || t.state === 'pending';

  const say = (sentence: string) => store.reportError('task', new Error(sentence));
  const verbs = {
    x: () => (open ? setAsk('done') : say(`task ${taskRef(t.id)} is already closed`)),
    u: () =>
      t.state === 'draft'
        ? queue.mutate({ tasks: [t.id] })
        : t.state === 'queued'
          ? draft.mutate({ tasks: [t.id] })
          : say('only a draft or a queued task can move'),
    r: () => (t.state === 'done' && mine ? setAsk('reopen') : say(REOPEN_ONLY)),
    a: () => commentBox.current?.querySelector<HTMLElement>('[contenteditable]')?.focus(),
    o: () => open && setAsk('assign'),
    d: () => (open ? setAsk('discard') : say(`task ${taskRef(t.id)} is already closed`)),
    w: () => startable && mine && setAsk('withdraw'),
    c: () => open && setAsk('cancel'),
    l: () => (running ? setAsk('release') : say(`task ${taskRef(t.id)} is not running`)),
  } as const;
  const key = (e: KeyboardEvent) => {
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'Escape') return ask ? setAsk(null) : goTasks();
    const f = (verbs as Record<string, () => void>)[e.key];
    if (!f) return;
    e.preventDefault();
    f();
  };
  // Focus the detail so its keys work as soon as it opens.
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => root.current?.focus({ preventScroll: true }), [t.id]);

  const why = (t.status ? (HELD.includes(t.status.state) ? t.status.text : undefined) : t.waiting?.text) ?? t.hold_reason ?? t.block_reason;
  const yes = { ok: true, why: undefined };
  const item = (label: string, key: string, run: () => void, can: { ok: boolean; why?: string }) => ({ label, key, run, can });
  // What changes the status, in the dropdown on the state; the one that fits best is also the main button.
  const status = [
    ...(open && t.state === 'draft' ? [item('Queue', 'u', verbs.u, can.queue)] : []),
    ...(open && t.state === 'queued' ? [item('Back to draft', 'u', verbs.u, can.draft)] : []),
    ...(open ? [item('Done', 'x', verbs.x, can.done)] : []),
    ...(t.state === 'done' && mine ? [item('Reopen', 'r', verbs.r, can.reopen)] : []),
    ...(running ? [item('Release', 'l', verbs.l, can.release)] : []),
  ];
  const first = status.find((v) => v.label !== 'Release');
  const main = first && { ...first, busy: queue.isPending || draft.isPending };
  const more = [
    ...status.filter((v) => v !== first),
    ...(startable && mine ? [item('Withdraw', 'w', verbs.w, can.withdraw)] : []),
    ...(open ? [item('Discard', 'd', verbs.d, can.discard), item('Assign', 'o', verbs.o, edit.can)] : []),
    'sep' as const,
    item(showHistory ? 'Hide history' : 'Show history', '', () => setShowHistory(!showHistory), yes),
  ];
  const reasonLabel = (verb: string) => (running ? `stop it and discard - why:` : `${verb} - why:`);
  const mark = decided ? 'decided' : authored || t.id < 0 ? 'authored' : null;
  return (
    <div className="tbody flex min-h-full flex-col" ref={root} tabIndex={-1} onKeyDown={key}>
      <header className="tdetail-head flex flex-wrap items-baseline gap-2">
        <h2 className="m-0 min-w-0 text-title font-semibold">
          <span className="mono">{taskRef(t.id)}</span>{' '}
          <Inline
            can={edit.can.ok && open}
            label="Title"
            input={(close) => <TextInput value={t.title} label="Title" onSave={(title) => edit.save({ title })} onClose={close} />}
          >
            {t.title}
          </Inline>
        </h2>
        {status.length ? (
          <MenuButton
            label="Status"
            variant="outline"
            size="sm"
            align="start"
            trigger={<StateChip state={t.state} mark={mark} word to={t.to} result={watchResult(t)} />}
          >
            {status.map((v) => (
              <MenuItem key={v.label} disabled={!v.can.ok} title={v.can.why} onSelect={v.run}>
                {v.label}
              </MenuItem>
            ))}
          </MenuButton>
        ) : (
          <StateChip state={t.state} mark={mark} word to={t.to} result={watchResult(t)} />
        )}
      </header>
      <p className="m-0 mt-1 text-hint text-ink-3">
        Asked by <AgentLink name={t.from} /> · created {dateTime(t.created)}
        {t.done ? ` · closed ${dateTime(t.done)}` : ''}
        {t.parent ? (
          <>
            {' '}
            · under{' '}
            <a href={`#/tasks/${t.parent}`} className="mono">
              {taskRef(t.parent)}
            </a>
          </>
        ) : null}
      </p>
      <TaskWatch t={t} />
      {why ? (
        <p className="twhy m-0 mt-3 rounded-card border border-wait px-3 py-2 text-wait" role="status">
          <RichText text={why} />
        </p>
      ) : null}
      <div className="mt-3">
        <TaskChips t={t} now={now} onAssign={() => setAsk('assign')} />
      </div>
      <Brief t={t} can={edit.can.ok && open} />

      {t.blockers.length ? <TaskLinks icon="waiting" label="Waits on" items={t.blockers} /> : null}
      {t.children.length ? <TaskLinks icon="tasks" label="Sub-tasks" items={t.children.map((c) => ({ task: c }))} /> : null}

      {t.evidence ? (
        <>
          <h3 className={H3}>Evidence</h3>
          <p className="tbody-text mono m-0 whitespace-pre-wrap">
            <RichText text={t.evidence} />
          </p>
        </>
      ) : null}

      <Activity t={t} box={commentBox} />
      {showHistory ? <HistorySection id={t.id} /> : null}

      <div className="sticky bottom-0 z-[2] -mx-4 mt-auto -mb-3 border-t border-line bg-canvas px-4 py-2">
        {ask === 'done' ? (
          <ReasonSheet
            label="done: what came of it?"
            submit="Done"
            onCancel={() => setAsk(null)}
            onSubmit={(evidence) => (setAsk(null), done.mutate({ task: t.id, evidence, reason: true }))}
          />
        ) : null}
        {ask === 'reopen' ? (
          <ReasonSheet
            label="reopen: why?"
            submit="Reopen"
            onCancel={() => setAsk(null)}
            onSubmit={(note) => (setAsk(null), reopen.mutate({ task: t.id, note }))}
          />
        ) : null}
        {ask === 'discard' ? (
          <ReasonSheet
            label={reasonLabel('discard')}
            submit="Discard"
            onCancel={() => setAsk(null)}
            onSubmit={(reason) => (setAsk(null), discard.mutate({ task: t.id, reason }))}
          />
        ) : null}
        {ask === 'withdraw' ? (
          <Sheet
            label={`withdraw ${taskRef(t.id)}?`}
            submit="Withdraw"
            onCancel={() => setAsk(null)}
            onSubmit={() => (setAsk(null), withdraw.mutate({ task: t.id }))}
          >
            <span className="muted">Takes the task back before anyone has started it.</span>
          </Sheet>
        ) : null}
        {ask === 'cancel' ? (
          <CancelSheet
            running={running}
            id={t.id}
            onCancel={() => setAsk(null)}
            onSubmit={(reason) => (setAsk(null), cancel.mutate({ task: t.id, reason }))}
          />
        ) : null}
        {ask === 'release' ? (
          <ReasonSheet
            label={`release ${taskRef(t.id)} - why:`}
            submit="Release"
            onCancel={() => setAsk(null)}
            onSubmit={(reason) => (setAsk(null), release.mutate({ task: t.id, reason }))}
          />
        ) : null}
        {ask === 'assign' ? <AssignSheet t={t} me={me} onClose={() => setAsk(null)} /> : null}

        <div className="tverbs flex flex-wrap items-center gap-1.5" role="toolbar" aria-label="Task actions">
          {main ? (
            <Button
              variant="solid"
              label={main.label}
              title={main.can.why ?? `${main.label} (${main.key})`}
              disabled={!main.can.ok || main.busy}
              className="max-sm:min-h-[44px]"
              onClick={main.run}
            >
              {main.busy ? '…' : main.label}
            </Button>
          ) : null}
          {open ? (
            <Button
              variant="outline"
              label="Cancel task"
              title={can.cancel.why ?? 'Cancel task (c)'}
              disabled={!can.cancel.ok}
              className="max-sm:min-h-[44px]"
              onClick={verbs.c}
            >
              Cancel task
            </Button>
          ) : null}
          <MenuButton label="More" trigger="More ▾" variant="outline" className="max-sm:min-h-[44px]">
            {more.map((v) =>
              v === 'sep' ? (
                <MenuSeparator key="sep" />
              ) : (
                <MenuItem key={v.label} disabled={!v.can.ok} title={v.can.why} onSelect={v.run}>
                  {v.label}
                </MenuItem>
              ),
            )}
          </MenuButton>
        </div>
      </div>
    </div>
  );
}

/** The brief, folded to a few lines until "Show more"; a click on the text edits it. */
function Brief({ t, can }: { t: Detail; can: boolean }) {
  const [more, setMore] = useState(false);
  const long = t.body.length > 280 || t.body.split('\n').length > 4;
  return (
    <div className="tbody-text mt-3">
      <Inline can={can} label="Description" input={(close) => <BodyInline t={t} onClose={close} />}>
        {t.body ? (
          <>
            <div className={long && !more ? 'line-clamp-4' : undefined}>
              <Markdown text={t.body} />
            </div>
            {long ? (
              <button type="button" className="border-0 bg-transparent p-0 text-link" onClick={() => setMore(!more)}>
                {more ? 'Show less' : 'Show more'}
              </button>
            ) : null}
          </>
        ) : (
          <span className="muted">Add a description</span>
        )}
      </Inline>
    </div>
  );
}

/** Every field changed, from task.history; it is read only once opened from More. */
function HistorySection({ id }: { id: number }) {
  const history = useTaskHistory(id);
  const rows = history.data?.rows ?? [];
  return rows.length ? (
    <section aria-label="History" className="mt-4">
      <h3 className="m-0 mb-1 text-ink-3">History · {rows.length} changes</h3>
      <TaskHistory rows={rows} />
    </section>
  ) : null;
}
