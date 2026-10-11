// One line of the conversation: the person's and the agent's messages with how far each got, and the transcript's tool, prompt, task and
// session lines as small cards. What a line is and where it stands is an icon with its words as the tooltip; a click on a message picks it
// to reply to (the reference the composer shows).
import { useState, type MouseEvent } from 'react';
import type { ChatMessage, Landing, TranscriptItem } from '@handline/client';
import { isOpenAsk, landingLabel, sideOf } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useAgents, useChatActions } from '@handline/app-data';
import { EditorButton } from './EditorButton.tsx';
import { Button, Dialog, Icon, type IconName } from './ui/index.ts';
import { PathText } from './PathText.tsx';

const clock = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const LANDING_ICON: Record<string, IconName> = {
  queued: 'queued',
  typed: 'typed',
  delivered: 'delivered',
  delivered_unanswered: 'unanswered',
  answered: 'answered',
  dismissed: 'dismissed',
  withdrawn: 'withdrawn',
};
export const landingIcon = (l: Landing): IconName => LANDING_ICON[l] ?? 'delivered';

/** Scrolls the message with this number into view and flashes it; false when it is not drawn (another page, another tab). */
export function jumpTo(id: number): boolean {
  const el = document.querySelector<HTMLElement>(`.msg[data-id="${id}"]`);
  if (!el) return false;
  el.scrollIntoView?.({ block: 'center' });
  el.setAttribute('data-flash', '');
  setTimeout(() => el.removeAttribute('data-flash'), 1200);
  return true;
}

/** Who a reply to this message goes to: the agent that said it, else the one it was said to. */
export const replyTo = (m: ChatMessage, isAgent: (n: string) => boolean): string => (isAgent(m.from) ? m.from : m.to);

const full = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? undefined : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
};

/** Text with its paths made clickable (when the shell can open them) and a message number as a link to that message. */
export function RefText({ text, agent, onRef }: { text: string; agent?: string; onRef?: (id: number) => void }) {
  if (!onRef || !/#\d/.test(text)) return <PathText text={text} agent={agent} />;
  return (
    <>
      {text.split(/(?<![\w&])(#\d{1,9})\b/).map((part, i) =>
        /^#\d+$/.test(part) ? (
          <button key={i} type="button" className="ref" title={`Go to ${part}`} onClick={() => onRef(Number(part.slice(1)))}>
            {part}
          </button>
        ) : (
          <PathText key={i} text={part} agent={agent} />
        ),
      )}
    </>
  );
}

// A message to a structured agent says how far into the CLI it is; `unknown` is never resent, so the person is told it may have run.
const RECEIPT: Record<string, { icon: IconName; label: string }> = {
  queued: { icon: 'queued', label: 'Queued: sent when the running turn ends' },
  sent: { icon: 'typed', label: 'Sent to the agent, not confirmed yet' },
  confirmed: { icon: 'delivered', label: 'Confirmed by the agent' },
  unknown: { icon: 'maybeRan', label: 'May or may not have run' },
};
const receiptOf = (state: string) => RECEIPT[state] ?? RECEIPT.sent!;

const snippet = (body: string) => (body.length > 80 ? `${body.slice(0, 80)}…` : body);

export function Message(props: {
  m: ChatMessage;
  agent: string;
  /** The message a reply is being written to. */
  picked?: boolean;
  onPick?: (m: ChatMessage) => void;
  /** The messages the page holds, to say what a reply refers to. */
  byId?: Map<number, ChatMessage>;
  /** The agent is mid-turn: a queued message can be typed at it now. */
  working?: boolean;
  /** Puts a taken-back message's words in the box again. */
  onRestore?: (text: string) => void;
  /** A message number in the text was clicked. */
  onRef?: (id: number) => void;
}) {
  const { m, agent } = props;
  const act = useChatActions(agent);
  const can = useSelector((s) => ({ answer: s.can('chat.answer'), dismiss: s.can('chat.dismiss') }));
  const names = new Set((useAgents().data?.agents ?? []).map((a) => a.name));
  const side = sideOf(m, agent, (n) => names.has(n));
  const [answer, setAnswer] = useState('');
  const [ask, setAsk] = useState(false);
  const unnumbered = m.id < 0; // shown before the daemon numbered it (or failed): nothing can be done to it but Retry and Discard
  const failed = m.state === 'failed';
  const parent = m.parent != null ? props.byId?.get(m.parent) : undefined;
  const open = isOpenAsk(m, agent) && can.answer;
  const mine = side === 'person' && !unnumbered && !failed;
  // Not answered yet and not already taken back: the person's own undo. A message the agent has not got just goes; one it has asks first.
  const undoable = mine && ['queued', 'typed', 'delivered', 'delivered_unanswered'].includes(m.landing);
  const got = m.receipt ? m.receipt.state !== 'queued' : m.landing === 'delivered' || m.landing === 'delivered_unanswered';
  // The words come back to the box only once the daemon has taken the message back; a refusal leaves everything as it was.
  const takeBack = (confirm = false) => {
    setAsk(false);
    act
      .cancel(m.id, confirm)
      .then(() => props.onRestore?.(m.body))
      .catch(() => undefined);
  };
  // A click on the message, not on a control in it, picks it to reply to.
  const pick = (e: MouseEvent) => {
    if (unnumbered || failed || !props.onPick || (e.target as HTMLElement).closest('button, a, input, textarea, form')) return;
    props.onPick(m);
  };
  return (
    <div
      className={`msg ${side}${failed ? ' failed' : ''}${props.picked ? ' picked' : ''}${unnumbered || failed ? '' : ' pickable'}`}
      data-landing={m.landing}
      data-id={m.id}
      title={full(m.at)}
      data-pending={unnumbered && !failed ? 'true' : undefined}
      aria-current={props.picked ? 'true' : undefined}
      onClick={pick}
    >
      {side === 'other' ? (
        <div className="msg-from muted">
          {m.from} → {m.to}
        </div>
      ) : null}
      {m.parent != null ? (
        <button className="msg-ref" title={parent ? snippet(parent.body) : `#${m.parent}`} onClick={() => jumpTo(m.parent!)}>
          <Icon name="reference" size={12} label={`Reply to #${m.parent}`} />
          <span className="mono">#{m.parent}</span>
          {parent ? <span className="muted"> {snippet(parent.body)}</span> : null}
        </button>
      ) : null}
      <div className="msg-body">
        <RefText text={m.body} agent={agent} onRef={props.onRef} />
      </div>
      <div className="msg-meta muted">
        {!unnumbered ? <span className="mono">#{m.id}</span> : null}
        {side === 'person' && !failed && m.receipt ? (
          <span className="landing receipt" data-receipt={m.receipt.state} title={receiptOf(m.receipt.state).label}>
            {' '}
            <Icon name={receiptOf(m.receipt.state).icon} size={13} label={receiptOf(m.receipt.state).label} />
          </span>
        ) : side === 'person' && !failed ? (
          <span className="landing" title={m.sending_reason ? `${landingLabel(m.landing)}: ${m.sending_reason}` : landingLabel(m.landing)}>
            {' '}
            <Icon name={landingIcon(m.landing)} size={13} label={landingLabel(m.landing)} />
          </span>
        ) : null}
        {open ? (
          <span title="Waiting for you">
            {' '}
            <Icon name="ask" size={13} label="Waiting for your answer" />
          </span>
        ) : null}
        {m.tasks.map((t) => (
          <a key={t.id} className="msg-task mono" href={`#/tasks/${t.id}`} title={t.title}>
            {' '}
            <Icon name="tasks" size={12} label={`Task ${t.id}: ${t.title}`} />T{t.id}
          </a>
        ))}
      </div>
      {undoable ? (
        <div className="msg-acts">
          {m.landing === 'queued' && (m.receipt || props.working) ? (
            <Button size="sm" icon="sendNow" label="Send now" onClick={() => act.sendNow(m.id)} />
          ) : null}
          <Button size="sm" icon="discard" label="Take back" onClick={() => (got ? setAsk(true) : takeBack())} />
        </div>
      ) : null}
      {ask ? (
        <Dialog
          title="Take it back?"
          onClose={() => setAsk(false)}
          action={{ label: 'Take back', keys: '⌘↵', onClick: () => takeBack(true) }}
        >
          <p className="muted">
            {agent} already has this message. It is told to stop, and what waits behind it moves up. The words come back to the box.
          </p>
        </Dialog>
      ) : null}
      {m.receipt?.state === 'unknown' ? (
        <div className="msg-meta failed-line" role="note">
          <Icon name="maybeRan" size={13} /> This message may or may not have run. It is not sent again; send it again yourself if it
          matters.
        </div>
      ) : null}
      {failed ? (
        <div className="msg-meta failed-line" role="alert">
          <Icon name="failed" size={13} /> {m.sending_reason}{' '}
          <button className="icon" aria-label="Retry" title="Retry" onClick={() => act.retry(m.id)}>
            <Icon name="retry" size={14} />
          </button>
          <button className="icon" aria-label="Discard" title="Discard" onClick={() => act.discard(m.id)}>
            <Icon name="discard" size={14} />
          </button>
        </div>
      ) : null}
      {side === 'agent' && m.answer ? (
        <div className="msg person answer">
          <div className="msg-body">
            <PathText text={m.answer.text} agent={agent} />
          </div>
          <div className="msg-meta muted">
            {clock(m.answer.at)} <Icon name="answered" size={13} label="Your answer" />
          </div>
        </div>
      ) : null}
      {side === 'agent' && m.dismissed ? (
        <div className="msg-meta muted">
          <Icon name="dismissed" size={13} />
        </div>
      ) : null}
      {open ? (
        <form
          className="ask"
          onSubmit={(e) => {
            e.preventDefault();
            if (!answer.trim()) return;
            act.answer(m.id, answer);
            setAnswer('');
          }}
        >
          <input value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Answer" aria-label={`Answer to message ${m.id}`} />
          <EditorButton text={answer} onText={setAnswer} />
          <button className="icon" aria-label="Answer" title="Answer" disabled={!answer.trim()}>
            <Icon name="send" />
          </button>
          {can.dismiss ? (
            <button type="button" className="icon" aria-label="Dismiss" title="Dismiss" onClick={() => act.dismiss(m.id)}>
              <Icon name="dismiss" />
            </button>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}

/** A tool line: the command, and what it printed, in full, behind a button. */
function ToolLine({ i }: { i: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  const cut = i.dropped_lines ?? 0;
  return (
    <div className="line tool" data-kind="tool">
      <Icon name="tool" size={14} label={i.tool ?? 'Tool'} />
      <span className="mono"> {i.tool}</span>
      {i.text ? <span className="muted"> {i.text}</span> : null}
      {i.output ? (
        <Button variant="outline" size="sm" label="Output" icon={open ? 'less' : 'more'} onClick={() => setOpen(!open)}>
          {open ? 'Hide output' : 'Show all'}
        </Button>
      ) : null}
      {open ? <pre className="lines tool-output">{i.output}</pre> : null}
      {cut && (open || !i.output) ? (
        <span className="muted">
          {cut} more line{cut === 1 ? '' : 's'} let go
        </span>
      ) : null}
    </div>
  );
}

/** A session marker: the handoff's first line, and the whole of it, scrollable, behind a button. */
function SessionCard({ i }: { i: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  const handoff = i.session?.handoff;
  return (
    <div className="card card-session" data-kind="session">
      <Icon name="session" />
      <span className="card-what muted">{handoff ? `Handoff: ${handoff}` : null}</span>
      {handoff ? (
        <Button variant="outline" size="sm" label="Whole handoff" icon={open ? 'less' : 'more'} onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Whole handoff'}
        </Button>
      ) : null}
      <span className="muted card-time">{clock(i.at)}</span>
      {open ? <pre className="lines handoff-full">{handoff}</pre> : null}
    </div>
  );
}

const TASK_EVENT: Record<string, IconName> = { opened: 'opened', changed: 'changed', done: 'done', withdrawn: 'withdrawn' };
const TASK_EVENT_WORD: Record<string, string> = { opened: 'opened', changed: 'changed', done: 'done', withdrawn: 'withdrawn' };

/** A line of the transcript that is not a message: the agent's reply and its tool, prompt, task and session lines. */
export function Item({ i }: { i: TranscriptItem }) {
  if (i.kind === 'reply')
    return (
      <div className="msg agent" data-kind="reply" title={full(i.at)}>
        <div className="msg-body">
          <PathText text={i.text ?? ''} />
        </div>
      </div>
    );
  if (i.kind === 'tool') return <ToolLine i={i} />;
  if (i.kind === 'prompt') {
    const p = i.prompt;
    const name = p?.kind === 'permission' ? 'permission' : 'prompt';
    const what = p?.kind === 'permission' ? `Asked permission: ${p.tool ?? ''}` : `Asked: ${p?.kind ?? 'a dialog'}`;
    return (
      <div className={`card card-prompt${i.open ? ' open' : ''}`} data-kind="prompt" data-open={i.open ? '' : undefined}>
        <Icon name={name} label={what} />
        <span className="card-what">
          {p?.kind === 'permission' ? <span className="mono">{p.tool}</span> : <span className="muted">{p?.kind}</span>}
          {p?.command ? <code className="card-code">{p.command}</code> : null}
        </span>
        <span className="card-state" title={i.open ? 'Waiting for you' : 'Answered'}>
          <Icon name={i.open ? 'waiting' : 'answered'} size={14} label={i.open ? 'Waiting for you' : 'Answered'} />
        </span>
        <span className="muted card-time">{clock(i.at)}</span>
      </div>
    );
  }
  if (i.kind === 'session') return <SessionCard i={i} />;
  const e = i.task_event;
  const ev = e?.event ?? 'changed';
  const word = `Task ${e?.task} ${TASK_EVENT_WORD[ev] ?? ev}`;
  return (
    <div className="card card-task" data-kind="task_event" data-event={ev}>
      <Icon name={TASK_EVENT[ev] ?? 'changed'} label={word} />
      <a className="mono" href={`#/tasks/${e?.task}`} title={word}>
        T{e?.task}
      </a>
      {e?.evidence ? <span className="card-what muted">{e.evidence}</span> : <span className="card-what" />}
      <span className="muted card-time">{clock(i.at)}</span>
    </div>
  );
}
