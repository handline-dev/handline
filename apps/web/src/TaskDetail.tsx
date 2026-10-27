// One task: title, status and a History button, the Time line, why it waits, assignee, priority and time box as chips, the brief (folded), what it
// waits on and what hangs under it, one Activity list, and along the bottom Done, Assign, Comment, Cancel and More. A verb that does not apply is
// hidden; one the daemon does not serve yet is shown disabled.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { dateTime, HELD, isPerson, taskRef, watchResult } from '@handline/app-core';
import { useMe, useStore } from '@handline/app-react';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, usePending, useTask, useTaskHistory, useWrite } from '@handline/app-data';
import { Button, Icon, MenuButton, MenuItem, Popup } from '#src/ui/index.ts';
import { AssignPopover } from '#src/TaskPeople.tsx';
import { leave } from '#src/route.ts';
import { StateChip, useCan, useNow } from '#src/taskui.tsx';
import { AskSheets, type Ask } from '#src/TaskAsk.tsx';
import { TaskChips, TaskWatch, TaskLinks } from '#src/TaskDetailParts.tsx';
import { AgentLink, BodyInline, Inline, TextInput, useEditTask } from '#src/TaskInline.tsx';
import { TaskHistory } from '#src/TaskHistory.tsx';
import { TimeLine } from '#src/TaskTime.tsx';
import { Activity } from '#src/TaskActivity.tsx';
import { Markdown } from '#src/Markdown.tsx';
import { RichText } from '#src/RichText.tsx';
import { View } from '#src/View.tsx';
import { typing } from '#src/typing.ts';

const CLOSED = ['done', 'discarded'];
/** The bridge's own sentence for a key that does not apply. */
const REOPEN_ONLY = 'only a done task that you asked for can be reopened';
const H3 = 'mt-4 mb-1 flex items-center text-ink-3';

/** The daemon's reason for a task it has not yet looked at: not something the person can act on, so it reads as a quiet line. */
const NOT_PICKED = /has not looked at it yet/;

export function TaskDetail({
  id,
  onBack,
  embedded,
  drawer,
}: {
  id: number;
  /** A close for a detail the window's own Back cannot return from; left out, the header is not drawn. */
  onBack?: () => void;
  /** A pane inside its host (the Tasks tab of an agent pane, the Tasks page's split). */
  embedded?: boolean;
  /** The column beside the Tasks table, full height of the split: only the Tasks page asks for it. */
  drawer?: boolean;
}) {
  const q = useTask(id);
  const gone = q.isError && classify(q.error).kind === 'not_found';
  return (
    <article
      className={`tdetail flex min-h-0 min-w-0 flex-col overflow-auto px-4 py-3 outline-none max-sm:px-4 max-sm:py-3${
        drawer
          ? ' w-[min(480px,45%)] flex-none border-l border-line bg-surface'
          : embedded
            ? ' pane relative flex-1 bg-panel -outline-offset-1'
            : ' flex-1 mx-auto w-full max-w-[var(--w-page)] bg-canvas'
      }`}
      aria-label={`Task ${taskRef(id)}`}
    >
      {onBack ? (
        <div className="mb-4 flex items-center justify-end gap-2">
          <Button icon="close" label="Back" title="Back (Esc)" className="back" onClick={onBack}>
            Back
          </Button>
        </div>
      ) : null}
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
  const me = useMe();
  const [ask, setAsk] = useState<Ask>(null);
  const commentBox = useRef<HTMLDivElement>(null);
  const edit = useEditTask(t);
  const queue = useWrite('task.queue');
  const draft = useWrite('task.draft');
  const cancel = useWrite('task.cancel');
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
    if (e.key === 'Escape') return ask ? setAsk(null) : leave('#/tasks');
    const f = (verbs as Record<string, () => void>)[e.key];
    if (!f) return;
    e.preventDefault();
    f();
  };
  // Focus the detail so its keys work as soon as it opens.
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => root.current?.focus({ preventScroll: true }), [t.id]);

  // The state chip already says it waits on a person, so the box leaves that out and keeps any other reason.
  const waitsOnTask = t.waiting && t.waiting.on !== 'founder' && !isPerson(t.waiting.on, me) ? t.waiting.text : undefined;
  const heldText = t.status && HELD.includes(t.status.state) && t.status.reason !== 'you' ? t.status.text : undefined;
  const why = (t.status ? heldText : waitsOnTask) ?? t.hold_reason ?? t.block_reason;
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
    ...(open ? [item('Discard', 'd', verbs.d, can.discard)] : []),
  ];
  const mark = decided ? 'decided' : authored || t.id < 0 ? 'authored' : null;
  return (
    <div className="tbody flex min-h-full flex-col bg-inherit" ref={root} tabIndex={-1} onKeyDown={key}>
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
        <HistoryButton id={t.id} />
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
      <TimeLine id={t.id} />
      <TaskWatch t={t} />
      {why && NOT_PICKED.test(why) ? (
        <p className="twhy m-0 mt-2 flex items-center gap-1.5 text-hint text-ink-3" role="status">
          <Icon name="queued" size={14} aria-hidden />
          Not picked up yet
        </p>
      ) : why ? (
        <p className="twhy m-0 mt-3 rounded-card border border-wait px-3 py-2 text-wait" role="status">
          <RichText text={why} />
        </p>
      ) : null}
      <div className="mt-3">
        <TaskChips t={t} now={now} me={me} />
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

      {/* A sticky bar stops above the scroll box's 12 px bottom padding; -bottom-3 takes it to the edge. */}
      <div className="task-footer sticky -bottom-3 z-[2] -mx-4 mt-auto -mb-3 border-t border-line bg-inherit px-4 pb-[calc(0.5rem+env(safe-area-inset-bottom))] pt-1.5">
        <AskSheets t={t} ask={ask} running={running} cancel={cancel} onClose={() => setAsk(null)} />

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
            <span className="relative inline-flex">
              <Button
                variant="outline"
                label="Assign"
                title={edit.can.why ?? 'Assign (o)'}
                disabled={!edit.can.ok}
                className="max-sm:min-h-[44px]"
                onClick={() => setAsk(ask === 'assign' ? null : 'assign')}
              >
                Assign
              </Button>
              <AssignPopover t={t} me={me} open={ask === 'assign'} onClose={() => setAsk(null)} up />
            </span>
          ) : null}
          <Button variant="outline" label="Comment" title="Comment (a)" className="max-sm:min-h-[44px]" onClick={verbs.a}>
            Comment
          </Button>
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
            {more.map((v) => (
              <MenuItem key={v.label} disabled={!v.can.ok} title={v.can.why} onSelect={v.run}>
                {v.label}
              </MenuItem>
            ))}
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

/** The History button in the header: every field changed, from task.history, in a popover with its filters; read only once opened. */
function HistoryButton({ id }: { id: number }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative ml-auto">
      <Button icon="history" label="History" title="History" aria-expanded={open} onClick={() => setOpen(!open)} />
      <Popup open={open} onClose={() => setOpen(false)} title="History" align="right" bare>
        <div className="max-h-[60vh] w-[min(420px,90vw)] overflow-auto p-2">
          <HistoryRows id={id} />
        </div>
      </Popup>
    </span>
  );
}

function HistoryRows({ id }: { id: number }) {
  const rows = useTaskHistory(id).data?.rows ?? [];
  return rows.length ? <TaskHistory rows={rows} /> : <p className="m-0 text-ink-3">No changes yet.</p>;
}
