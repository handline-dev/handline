// One task: its title, state, people and timing, the body, the delivery history, evidence, a reply box, what it waits on and what hangs under it,
// and the verbs of the bridge's task detail. A verb that does not apply is hidden; one the daemon does not serve yet is shown disabled.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { taskRef } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, usePending, useTask, useWrite } from '@handline/app-data';
import { Button, Icon } from './ui/index.ts';
import { goTasks } from './route.ts';
import { ReasonSheet, Sheet, StateChip, useCan, useNow } from './taskui.tsx';
import { AssignSheet, ReplyBox } from './TaskPeople.tsx';
import { CancelSheet, EditForm, TaskLinks, TaskMeta, TaskTimeline, Verb } from './TaskDetailParts.tsx';
import { View } from './View.tsx';

type Ask = null | 'done' | 'reopen' | 'discard' | 'cancel' | 'withdraw' | 'edit' | 'release' | 'assign';

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
        embedded ? ' pane relative bg-panel -outline-offset-1' : ''
      }${
        drawer
          ? ' absolute top-[calc(var(--sp-5)+var(--h-btn)+var(--sp-4))] right-0 bottom-0 left-auto z-[5] w-[min(480px,100%)] border-l border-line bg-surface shadow-[var(--shadow-pop)]'
          : ''
      }`}
      aria-label={`Task ${taskRef(id)}`}
    >
      <Button icon="close" label="Back" title="Back (Esc)" className="back self-end mb-4" onClick={onBack}>
        Back
      </Button>
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
  const replyBox = useRef<HTMLTextAreaElement>(null);
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
    edit: useCan('task.edit'),
    reply: useCan('chat.send'),
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
    a: () => replyBox.current?.focus(),
    o: () => open && setAsk('assign'),
    d: () => (open ? setAsk('discard') : say(`task ${taskRef(t.id)} is already closed`)),
    e: () => open && setAsk('edit'),
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
    <div className="tbody" ref={root} tabIndex={-1} onKeyDown={key}>
      <header className="tdetail-head flex flex-wrap items-baseline gap-2">
        <h2 className="m-0 text-title font-semibold">
          <span className="mono">{taskRef(t.id)}</span> {t.title}
        </h2>
        <StateChip state={t.state} mark={mark} word />
      </header>
      <TaskMeta t={t} now={now} />
      <div
        className="tverbs flex flex-wrap gap-1.5 my-2 max-sm:sticky max-sm:bottom-0 max-sm:mt-auto max-sm:-mx-4 max-sm:-mb-3 max-sm:px-4 max-sm:py-2 max-sm:bg-canvas max-sm:border-t max-sm:border-line"
        role="toolbar"
        aria-label="Task actions"
      >
        {open && t.state === 'draft' ? (
          <Verb k="u" icon="taskQueued" label="Queue" can={can.queue} onClick={verbs.u} busy={queue.isPending} />
        ) : null}
        {open && t.state === 'queued' ? (
          <Verb k="u" icon="taskDraft" label="Back to draft" can={can.draft} onClick={verbs.u} busy={draft.isPending} />
        ) : null}
        {open ? <Verb k="x" icon="taskDone" label="Done" can={can.done} onClick={verbs.x} /> : null}
        {t.state === 'done' && mine ? <Verb k="r" icon="retry" label="Reopen" can={can.reopen} onClick={verbs.r} /> : null}
        {open ? <Verb k="o" icon="agent" label="Assign" can={can.edit} onClick={verbs.o} /> : null}
        <Verb k="a" icon="say" label="Reply" can={can.reply} onClick={verbs.a} />
        {open ? <Verb k="e" icon="answer" label="Edit" can={can.edit} onClick={verbs.e} /> : null}
        {startable && mine ? <Verb k="w" icon="withdrawn" label="Withdraw" can={can.withdraw} onClick={verbs.w} /> : null}
        {running ? <Verb k="l" icon="retry" label="Release" can={can.release} onClick={verbs.l} busy={release.isPending} /> : null}
        {open ? <Verb k="c" icon="cancel" label="Cancel" can={can.cancel} onClick={verbs.c} /> : null}
        {open ? <Verb k="d" icon="dismiss" label="Discard" can={can.discard} onClick={verbs.d} /> : null}
      </div>
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
        <Sheet
          label={`release ${taskRef(t.id)}?`}
          submit="Release"
          onCancel={() => setAsk(null)}
          onSubmit={() => (setAsk(null), release.mutate({ task: t.id }))}
        >
          <span className="muted">Takes the task back to queued so another run can pick it up.</span>
        </Sheet>
      ) : null}
      {ask === 'assign' ? <AssignSheet t={t} me={me} onClose={() => setAsk(null)} /> : null}
      {ask === 'edit' ? <EditForm t={t} onClose={() => setAsk(null)} /> : null}

      {t.body ? <p className="tbody-text m-0 whitespace-pre-wrap">{t.body}</p> : null}

      {t.blockers.length ? <TaskLinks icon="waiting" label="Waits on" items={t.blockers} /> : null}
      {t.children.length ? <TaskLinks icon="tasks" label="Sub-tasks" items={t.children.map((c) => ({ task: c }))} /> : null}

      {t.deliveries.length || t.events.length ? (
        <>
          <h3 className={H3}>
            <Icon name="history" label="History" aria-hidden="true" /> <span className="ml-1">History</span>
          </h3>
          <TaskTimeline deliveries={t.deliveries} events={t.events} />
        </>
      ) : null}

      {t.evidence ? (
        <>
          <h3 className={H3}>Evidence</h3>
          <p className="tbody-text mono m-0 whitespace-pre-wrap">{t.evidence}</p>
        </>
      ) : null}
      {t.hold_reason || t.block_reason ? <p className="muted">{t.hold_reason ?? t.block_reason}</p> : null}

      <ReplyBox t={t} me={me} box={replyBox} />
    </div>
  );
}
