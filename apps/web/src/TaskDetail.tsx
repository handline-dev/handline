// One task: its title, state, people and timing, the body, the delivery history, evidence, what it waits on and what hangs under it, its
// comments, and the verbs of the bridge's task detail along the bottom. The title, body, owner, priority and time box change where they are.
// A verb that does not apply is hidden; one the daemon does not serve yet is shown disabled.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { taskRef } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, usePending, useTask, useTaskHistory, useWrite } from '@handline/app-data';
import { canCapture, requestCapture } from './Capture.tsx';
import { Button, Icon } from './ui/index.ts';
import { goTasks } from './route.ts';
import { ReasonSheet, Sheet, StateChip, useCan, useNow } from './taskui.tsx';
import { AssignSheet } from './TaskPeople.tsx';
import { CancelSheet, TaskLinks, TaskMeta, TaskTimeline, Verb } from './TaskDetailParts.tsx';
import { AgentLink, BodyInline, Inline, TextInput, useEditTask } from './TaskInline.tsx';
import { TaskHistory } from './TaskHistory.tsx';
import { Comments } from './TaskComments.tsx';
import { RichText } from './RichText.tsx';
import { View } from './View.tsx';

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
        {canCapture() ? <Button icon="select" label="Capture" title="Capture a window (⌘⇧C)" onClick={requestCapture} /> : null}
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
  const history = useTaskHistory(t.id);
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
        <StateChip state={t.state} mark={mark} word to={t.to} />
      </header>
      <TaskMeta t={t} now={now} onAssign={() => setAsk('assign')} />
      <div className="tbody-text mt-3 whitespace-pre-wrap">
        <Inline can={edit.can.ok && open} label="Description" input={(close) => <BodyInline t={t} onClose={close} />}>
          {t.body ? <RichText text={t.body} /> : <span className="muted">Add a description</span>}
        </Inline>
      </div>

      {t.blockers.length ? <TaskLinks icon="waiting" label="Waits on" items={t.blockers} /> : null}
      {t.children.length ? <TaskLinks icon="tasks" label="Sub-tasks" items={t.children.map((c) => ({ task: c }))} /> : null}

      {t.deliveries.length || t.events.length || history.data?.rows.length ? (
        <>
          <h3 className={H3}>
            <Icon name="history" label="History" aria-hidden="true" /> <span className="ml-1">History</span>
            {history.data ? <span className="ml-2 text-ink-3">{history.data.rows.length} changes</span> : null}
          </h3>
          {history.data ? <TaskHistory rows={history.data.rows} /> : <TaskTimeline deliveries={t.deliveries} events={t.events} />}
        </>
      ) : null}

      {t.evidence ? (
        <>
          <h3 className={H3}>Evidence</h3>
          <p className="tbody-text mono m-0 whitespace-pre-wrap">
            <RichText text={t.evidence} />
          </p>
        </>
      ) : null}
      {t.hold_reason || t.block_reason ? (
        <p className="muted">
          <RichText text={(t.hold_reason ?? t.block_reason)!} />
        </p>
      ) : null}

      {t.handoffs.length ? (
        <>
          <h3 className={H3}>Handoffs</h3>
          <ul className="thandoffs m-0 list-none p-0">
            {t.handoffs.map((h) => (
              <li key={h.at} className="whitespace-pre-wrap">
                <AgentLink name={h.from} /> to <AgentLink name={h.to} />: <RichText text={h.note} />
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <Comments t={t} box={commentBox} />

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

        <div className="tverbs flex flex-wrap gap-1.5" role="toolbar" aria-label="Task actions">
          {open && t.state === 'draft' ? (
            <Verb k="u" icon="taskQueued" label="Queue" can={can.queue} onClick={verbs.u} busy={queue.isPending} />
          ) : null}
          {open && t.state === 'queued' ? (
            <Verb k="u" icon="taskDraft" label="Back to draft" can={can.draft} onClick={verbs.u} busy={draft.isPending} />
          ) : null}
          {open ? <Verb k="x" icon="taskDone" label="Done" can={can.done} onClick={verbs.x} /> : null}
          {t.state === 'done' && mine ? <Verb k="r" icon="retry" label="Reopen" can={can.reopen} onClick={verbs.r} /> : null}
          {startable && mine ? <Verb k="w" icon="withdrawn" label="Withdraw" can={can.withdraw} onClick={verbs.w} /> : null}
          {running ? <Verb k="l" icon="retry" label="Release" can={can.release} onClick={verbs.l} busy={release.isPending} /> : null}
          {open ? <Verb k="c" icon="cancel" label="Cancel" can={can.cancel} onClick={verbs.c} /> : null}
          {open ? <Verb k="d" icon="dismiss" label="Discard" can={can.discard} onClick={verbs.d} /> : null}
        </div>
      </div>
    </div>
  );
}
