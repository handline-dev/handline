// One task: its title, state, people and timing, the body, the delivery history, evidence, memos, what it waits on and what hangs under it,
// and the verbs of the bridge's task detail. A verb that does not apply is hidden; one the daemon does not serve yet is shown disabled.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { timeoutText } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, usePending, useTask, useWrite } from '@handline/app-data';
import { goTasks } from './route.ts';
import { ReasonSheet, Sheet, StateChip, useCan, useNow } from './taskui.tsx';
import { View } from './View.tsx';

type Ask = null | 'done' | 'reopen' | 'discard' | 'cancel' | 'withdraw' | 'edit';

const CLOSED = ['done', 'discarded'];
const typing = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
/** The bridge's own sentence for a key that does not apply. */
const REOPEN_ONLY = 'only a done task that you asked for can be reopened';

export function TaskDetail({ id, onBack, embedded }: { id: number; onBack: () => void; embedded?: boolean }) {
  const q = useTask(id);
  const gone = q.isError && classify(q.error).kind === 'not_found';
  return (
    <article className={`tdetail${embedded ? ' pane' : ''}`} aria-label={`Task #${id}`}>
      <button className="link back" onClick={onBack}>
        ← Back
      </button>
      {gone ? (
        <div className="empty">
          <p>This task no longer exists.</p>
        </div>
      ) : (
        <View q={q} served shape="taskDetail" what={`task #${id}`}>
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
  const memoBox = useRef<HTMLTextAreaElement>(null);
  const done = useWrite('task.done');
  const queue = useWrite('task.queue');
  const draft = useWrite('task.draft');
  const reopen = useWrite('task.reopen');
  const discard = useWrite('task.discard');
  const withdraw = useWrite('task.withdraw');
  const cancel = useWrite('task.cancel');
  const can = {
    done: useCan('task.done'),
    queue: useCan('task.queue'),
    draft: useCan('task.draft'),
    reopen: useCan('task.reopen'),
    discard: useCan('task.discard'),
    memo: useCan('task.memo.add'),
    edit: useCan('task.edit'),
    withdraw: useCan('task.withdraw'),
    cancel: useCan('task.cancel'),
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
    x: () => (open ? setAsk('done') : say(`task #${t.id} is already closed`)),
    u: () =>
      t.state === 'draft'
        ? queue.mutate({ tasks: [t.id] })
        : t.state === 'queued'
          ? draft.mutate({ tasks: [t.id] })
          : say('only a draft or a queued task can move'),
    r: () => (t.state === 'done' && mine ? setAsk('reopen') : say(REOPEN_ONLY)),
    a: () => memoBox.current?.focus(),
    d: () => (open ? setAsk('discard') : say(`task #${t.id} is already closed`)),
    e: () => open && setAsk('edit'),
    w: () => startable && mine && setAsk('withdraw'),
    c: () => open && setAsk('cancel'),
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
      <header className="tdetail-head">
        <h2>
          <span className="mono">#{t.id}</span> {t.title}
        </h2>
        <StateChip state={t.state} mark={mark} />
      </header>
      <p className="tmeta">
        <span>
          owner <strong>{t.to}</strong>
        </span>
        <span>
          asked by <strong>{t.from}</strong>
        </span>
        <span>priority P{t.priority ?? 2}</span>
        <span>time box {t.time_box ?? 'none'}</span>
        {timeoutText(t, now) ? <span>{timeoutText(t, now)}</span> : null}
        <span>created {when(t.created)}</span>
        {t.delivered ? <span>delivered {when(t.delivered)}</span> : null}
        {t.done ? <span>closed {when(t.done)}</span> : null}
        {t.parent ? (
          <span>
            under{' '}
            <a href={`#/tasks/${t.parent}`} className="mono">
              #{t.parent}
            </a>
          </span>
        ) : null}
      </p>
      <div className="tverbs" role="toolbar" aria-label="Task actions">
        {open && t.state === 'draft' ? <Verb k="u" label="Queue" can={can.queue} onClick={verbs.u} busy={queue.isPending} /> : null}
        {open && t.state === 'queued' ? (
          <Verb k="u" label="Back to draft" can={can.draft} onClick={verbs.u} busy={draft.isPending} />
        ) : null}
        {open ? <Verb k="x" label="Done" can={can.done} onClick={verbs.x} /> : null}
        {t.state === 'done' && mine ? <Verb k="r" label="Reopen" can={can.reopen} onClick={verbs.r} /> : null}
        <Verb k="a" label="Assign a note" can={can.memo} onClick={verbs.a} />
        {open ? <Verb k="e" label="Edit" can={can.edit} onClick={verbs.e} /> : null}
        {startable && mine ? <Verb k="w" label="Withdraw" can={can.withdraw} onClick={verbs.w} /> : null}
        {open ? <Verb k="c" label="Cancel" can={can.cancel} onClick={verbs.c} /> : null}
        {open ? <Verb k="d" label="Discard" can={can.discard} onClick={verbs.d} /> : null}
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
          label={`withdraw #${t.id}?`}
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
      {ask === 'edit' ? <EditForm t={t} onClose={() => setAsk(null)} /> : null}

      <h3>Body</h3>
      {t.body ? <p className="tbody-text">{t.body}</p> : <p className="muted">No body.</p>}

      {t.blockers.length ? (
        <>
          <h3>Waits on</h3>
          <ul className="tlinks">
            {t.blockers.map((b) => (
              <li key={b.task}>
                <a href={`#/tasks/${b.task}`} className="mono">
                  #{b.task}
                </a>{' '}
                <span className="muted">{b.reason}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {t.children.length ? (
        <>
          <h3>Sub-tasks</h3>
          <ul className="tlinks">
            {t.children.map((c) => (
              <li key={c}>
                <a href={`#/tasks/${c}`} className="mono">
                  #{c}
                </a>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3>Delivery history</h3>
      {t.deliveries.length || t.events.length ? (
        <ol className="ttimeline">
          {t.deliveries.map((d) => (
            <li key={d.at}>
              <span className="mono">{when(d.at)}</span> delivered by {d.how}: {d.outcome}
            </li>
          ))}
          {t.events.map((e, i) => (
            <li key={i}>
              <span className="mono">{when(e.at)}</span> {e.type}
              {e.agent ? ` · ${e.agent}` : ''}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">Not delivered yet.</p>
      )}

      {t.evidence ? (
        <>
          <h3>Evidence</h3>
          <p className="tbody-text mono">{t.evidence}</p>
        </>
      ) : null}
      {t.hold_reason || t.block_reason ? <p className="muted">{t.hold_reason ?? t.block_reason}</p> : null}

      <h3>Memos</h3>
      {t.memos.length ? (
        <ul className="tlinks">
          {t.memos.map((m) => (
            <li key={m.id}>
              <span className="mono">{when(m.at)}</span> <strong>{m.author}</strong>: {m.text}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No memos.</p>
      )}
      <MemoBox id={t.id} box={memoBox} can={can.memo} />
    </div>
  );
}

function MemoBox({ id, box, can }: { id: number; box: React.RefObject<HTMLTextAreaElement | null>; can: { ok: boolean; why?: string } }) {
  const add = useWrite('task.memo.add', String(id));
  const [text, setText] = useState('');
  return (
    <form
      className="tmemo"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) add.mutate({ task: id, text: text.trim() }, { onSuccess: () => setText('') });
      }}
    >
      <textarea
        ref={box}
        className="tinput"
        rows={2}
        value={text}
        disabled={!can.ok}
        title={can.why}
        placeholder="memo to no one"
        aria-label="Memo"
        onChange={(e) => setText(e.target.value)}
      />
      <button className="btn" disabled={!can.ok || !text.trim()} title={can.why}>
        Add memo
      </button>
    </form>
  );
}

function CancelSheet(props: { running: boolean; id: number; onCancel: () => void; onSubmit: (reason?: string) => void }) {
  const [reason, setReason] = useState('');
  return (
    <Sheet
      label={props.running ? `stop #${props.id}? why (optional):` : `cancel #${props.id}? why (optional):`}
      submit={props.running ? 'Stop it' : 'Cancel task'}
      onCancel={props.onCancel}
      onSubmit={() => props.onSubmit(reason.trim() || undefined)}
    >
      <input className="tinput" value={reason} aria-label="Reason" onChange={(e) => setReason(e.target.value)} />
    </Sheet>
  );
}

function EditForm({ t, onClose }: { t: Detail; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const [title, setTitle] = useState(t.title);
  const [body, setBody] = useState(t.body);
  const [owner, setOwner] = useState(t.to);
  const [box, setBox] = useState(t.time_box ?? '');
  const [priority, setPriority] = useState<1 | 2 | 3>(t.priority ?? 2);
  const save = () => {
    const change = {
      ...(title.trim() && title !== t.title ? { title: title.trim() } : {}),
      ...(body !== t.body ? { body } : {}),
      ...(owner.trim() && owner !== t.to ? { owner: owner.trim() } : {}),
      ...(box.trim() && box !== t.time_box ? { time_box: box.trim() } : {}),
      ...(priority !== (t.priority ?? 2) ? { priority } : {}),
    };
    if (!Object.keys(change).length) return onClose();
    edit.mutate({ task: t.id, ...change }, { onSuccess: onClose });
  };
  return (
    <Sheet label={`Edit #${t.id}`} submit="Save" onCancel={onClose} onSubmit={save} busy={edit.isPending}>
      <div className="tform">
        <label>
          Title
          <input className="tinput" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          Owner
          <input className="tinput" value={owner} onChange={(e) => setOwner(e.target.value)} />
        </label>
        <label className="wide">
          Body
          <textarea className="tinput" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <label>
          Time box
          <input className="tinput" value={box} onChange={(e) => setBox(e.target.value)} />
        </label>
        <label>
          Priority
          <select value={priority} onChange={(e) => setPriority(Number(e.target.value) as 1 | 2 | 3)}>
            <option value={1}>1 high</option>
            <option value={2}>2</option>
            <option value={3}>3 low</option>
          </select>
        </label>
      </div>
    </Sheet>
  );
}

function Verb(props: { k: string; label: string; can: { ok: boolean; why?: string }; onClick: () => void; busy?: boolean }) {
  return (
    <button className="btn" disabled={!props.can.ok || props.busy} title={props.can.why} onClick={props.onClick}>
      {props.busy ? '…' : props.label} <kbd>{props.k}</kbd>
    </button>
  );
}

const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};
