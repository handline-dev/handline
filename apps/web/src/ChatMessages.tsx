// One line of the conversation: the person's and the agent's messages with how far each got, and the transcript's tool, prompt, task and
// session lines as small cards. What a line is and where it stands is an icon with its words as the tooltip; a click on a message picks it
// to reply to (the reference the composer shows).
import { useState, type MouseEvent } from 'react';
import type { ChatMessage, Landing, TranscriptItem } from '@handline/client';
import { landingLabel, openTask, sendNote, sideOf, taskRow } from '@handline/app-core';
import { useAgents, useChatActions } from '@handline/app-data';
import { Button, Card, CardCode, CardWhat, CodeBlock, Icon, type IconName } from './ui/index.ts';
import { bubbleClass, MSG_BODY, MSG_FAILED, MSG_FROM, MSG_META } from './msgClass.ts';
import { PathText } from './PathText.tsx';
import { useWorkspace } from './Workspace.tsx';

const clock = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

const LANDING_ICON: Record<string, IconName> = {
  queued: 'queued',
  typed: 'typed',
  delivered: 'delivered',
  // Delivered but unanswered draws as delivered — the waiting is said in the tooltip, not drawn.
  delivered_unanswered: 'delivered',
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
          <button key={i} type="button" className="ref border-0 bg-transparent p-0 font-mono text-[12px] leading-4 text-link underline" title={`Go to ${part}`} onClick={() => onRef(Number(part.slice(1)))}>
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
  /** How many of the person's messages to this agent are queued ahead of this one. */
  behind?: number;
  /** Puts a taken-back message's words in the box again. */
  onRestore?: (text: string) => void;
  /** A message number in the text was clicked. */
  onRef?: (id: number) => void;
}) {
  const { m, agent } = props;
  const act = useChatActions(agent);
  const names = new Set((useAgents().data?.agents ?? []).map((a) => a.name));
  const side = sideOf(m, agent, (n) => names.has(n));
  const unnumbered = m.id < 0; // shown before the daemon numbered it (or failed): nothing can be done to it but Retry and Discard
  const failed = m.state === 'failed';
  const parent = m.parent != null ? props.byId?.get(m.parent) : undefined;
  const mine = side === 'person' && !unnumbered && !failed;
  // The person's own undo, only while the daemon can still withdraw it: once the agent has the message there is no button.
  const got = m.receipt ? m.receipt.state !== 'queued' : m.landing !== 'queued' && m.landing !== 'typed';
  const undoable = mine && !got && ['queued', 'typed'].includes(m.landing);
  const note = side === 'person' ? sendNote(m, props.behind ?? 0) : null;
  // The words come back to the box only once the daemon has taken the message back; a refusal leaves everything as it was.
  const takeBack = () => {
    act
      .cancel(m.id)
      .then(() => props.onRestore?.(m.body))
      .catch(() => undefined);
  };
  // A click on the message, not on a control in it, picks it to reply to.
  const pick = (e: MouseEvent) => {
    if (unnumbered || failed || !props.onPick || (e.target as HTMLElement).closest('button, a, input, textarea, form')) return;
    props.onPick(m);
  };
  return (
    <>
    <div
      className={bubbleClass(side, { failed, picked: props.picked, pickable: !unnumbered && !failed })}
      data-landing={m.landing}
      data-id={m.id}
      title={full(m.at)}
      data-pending={unnumbered && !failed ? 'true' : undefined}
      aria-current={props.picked ? 'true' : undefined}
      onClick={pick}
    >
      {side === 'other' ? (
        <div className={`${MSG_FROM} muted`}>
          {m.from} → {m.to}
        </div>
      ) : side === 'person' && m.to !== agent ? (
        <div className={`${MSG_FROM} muted`}>to {m.to}</div>
      ) : null}
      {m.parent != null ? (
        <button
          className="msg-ref mb-1 flex max-w-full cursor-pointer items-center gap-1 overflow-hidden text-ellipsis whitespace-nowrap border-0 bg-transparent px-1.5 py-0 text-left text-[12px] text-ink-2 [&_svg]:align-middle"
          title={parent ? snippet(parent.body) : `#${m.parent}`}
          onClick={() => jumpTo(m.parent!)}
        >
          <Icon name="reference" size={12} label={`Reply to #${m.parent}`} />
          <span className="mono">#{m.parent}</span>
          {parent ? <span className="muted"> {snippet(parent.body)}</span> : null}
        </button>
      ) : null}
      <div className={MSG_BODY}>
        <RefText text={m.body} agent={agent} onRef={props.onRef} />
      </div>
      {undoable ? (
        <div className="msg-acts mt-1 flex gap-1">
          {m.landing === 'queued' && (m.receipt || props.working) ? (
            <Button size="sm" icon="sendNow" label="Send now" onClick={() => act.sendNow(m.id)} />
          ) : null}
          <Button size="sm" icon="discard" label="Take back" onClick={takeBack} />
        </div>
      ) : null}
      {note ? (
        <div className={`${MSG_META} state-note`} role="status">
          {note}
        </div>
      ) : null}
      {m.receipt?.state === 'unknown' ? (
        <div className={MSG_FAILED} role="note">
          <Icon name="maybeRan" size={13} /> This message may or may not have run. It is not sent again; send it again yourself if it
          matters.
        </div>
      ) : null}
      {failed ? (
        <div className={MSG_FAILED} role="alert">
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
        <div className={bubbleClass('person', { nested: true })}>
          <div className={MSG_BODY}>
            <PathText text={m.answer.text} agent={agent} />
          </div>
          <div className={`${MSG_META} muted`}>
            {clock(m.answer.at)} <Icon name="answered" size={13} label="Your answer" />
          </div>
        </div>
      ) : null}
      {side === 'agent' && m.dismissed ? (
        <div className={`${MSG_META} muted`}>
          <Icon name="dismissed" size={13} />
        </div>
      ) : null}
      </div>
      {/* The stamp hangs in the gutter beside the bubble, out of the flow: an agent's to its right, the person's to its left — the bubble never resizes for it. */}
      <div
        className={`${MSG_META} absolute bottom-0 hidden items-center whitespace-nowrap gap-1.5 text-ink-3 peer-hover:flex peer-aria-current:flex ${
          side === 'agent' ? 'left-full ml-1.5' : 'right-full mr-1.5'
        }`}
      >
        {side === 'person' && !failed && m.receipt ? (
          <span className="landing receipt" data-receipt={m.receipt.state} title={receiptOf(m.receipt.state).label}>
            <Icon name={receiptOf(m.receipt.state).icon} size={13} label={receiptOf(m.receipt.state).label} />
          </span>
        ) : side === 'person' && !failed ? (
          <span
            className="landing"
            title={m.sending_reason ? `${landingLabel(m.landing)}: ${m.sending_reason}` : landingLabel(m.landing)}
          >
            <Icon name={landingIcon(m.landing)} size={13} label={landingLabel(m.landing)} />
          </span>
        ) : null}
        {m.tasks.map((t) => (
          <a key={t.id} className="msg-task mono text-inherit" href={`#/tasks/${t.id}`} title={t.title}>
            <Icon name="tasks" size={12} label={`Task ${t.id}: ${t.title}`} />T{t.id}
          </a>
        ))}
        {!unnumbered ? <span className="mono">#{m.id}</span> : null}
        <span>{clock(m.at)}</span>
      </div>
    </>
  );
}

/** A tool line: the command, and what it printed, in full, behind a button. */
function ToolLine({ i }: { i: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  const cut = i.dropped_lines ?? 0;
  return (
    <div className="line tool self-start font-mono text-[12px] text-ink-2" data-kind="tool">
      <Icon name="tool" size={14} label={i.tool ?? 'Tool'} />
      <span className="mono"> {i.tool}</span>
      {i.text ? <span className="muted"> {i.text}</span> : null}
      {i.output ? (
        <Button variant="outline" size="sm" label="Output" icon={open ? 'less' : 'more'} onClick={() => setOpen(!open)}>
          {open ? 'Hide output' : 'Show all'}
        </Button>
      ) : null}
      {open ? <CodeBlock className="tool-output basis-full max-h-[20lh] overflow-auto max-sm:whitespace-pre-wrap">{i.output}</CodeBlock> : null}
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
    <Card variant="chat" className="card-session flex-wrap" data-kind="session">
      <Icon name="session" />
      <CardWhat className="muted">{handoff ? `Handoff: ${handoff}` : null}</CardWhat>
      {handoff ? (
        <Button variant="outline" size="sm" label="Whole handoff" icon={open ? 'less' : 'more'} onClick={() => setOpen(!open)}>
          {open ? 'Hide' : 'Whole handoff'}
        </Button>
      ) : null}
      <span className="muted card-time">{clock(i.at)}</span>
      {open ? <CodeBlock className="handoff-full basis-full max-h-[20lh] overflow-auto max-sm:whitespace-pre-wrap">{handoff}</CodeBlock> : null}
    </Card>
  );
}

const TASK_EVENT: Record<string, IconName> = { opened: 'opened', changed: 'changed', done: 'done', withdrawn: 'withdrawn' };
const TASK_ACCENT: Record<string, string> = { opened: 'border-l-ok', changed: 'border-l-wait', done: 'border-l-ok', withdrawn: 'border-l-bad' };

/** A task added, edited, done or deleted: a compact bar unlike a message, with the change in brief; a click opens the task in the Tasks tab. */
function TaskLine({ i, titles }: { i: TranscriptItem; titles?: Map<number, string> }) {
  const { apply } = useWorkspace();
  const ev = i.task_event?.event ?? 'changed';
  const row = taskRow(i, titles);
  return (
    <button
      type="button"
      className={`card-task flex w-full cursor-pointer items-baseline gap-2 rounded-card border border-l-4 border-line bg-panel px-2.5 py-1 text-left text-body ${TASK_ACCENT[ev] ?? 'border-l-wait'}`}
      data-kind="task_event"
      data-event={ev}
      title={`Open T${row.task} in the Tasks tab`}
      onClick={() => apply((w) => openTask(w, row.task))}
    >
      <Icon name={TASK_EVENT[ev] ?? 'changed'} size={14} label={row.label} />
      <span className="card-event text-meta font-semibold uppercase tracking-wide">{row.label}</span>
      <span className="mono">T{row.task}</span>
      <span className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap font-medium">{row.title}</span>
      {row.detail ? <span className="muted max-w-[45%] overflow-hidden text-ellipsis whitespace-nowrap">{row.detail}</span> : null}
      <span className="muted card-time">{clock(i.at)}</span>
    </button>
  );
}

/** A transcript line that is not a message: a reply, tool, prompt, session or task line. */
export function Item({ i, titles }: { i: TranscriptItem; titles?: Map<number, string> }) {
  if (i.kind === 'reply')
    return (
      <div className={bubbleClass('agent')} data-kind="reply" title={full(i.at)}>
        <div className={MSG_BODY}>
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
      <Card variant="chat" className="card-prompt" data-kind="prompt" data-open={i.open ? '' : undefined}>
        <Icon name={name} label={what} />
        <CardWhat>
          {p?.kind === 'permission' ? <span className="mono">{p.tool}</span> : <span className="muted">{p?.kind}</span>}
          {p?.command ? <CardCode>{p.command}</CardCode> : null}
        </CardWhat>
        <span className="card-state" title={i.open ? 'Waiting for you' : 'Answered'}>
          <Icon name={i.open ? 'waiting' : 'answered'} size={14} label={i.open ? 'Waiting for you' : 'Answered'} />
        </span>
        <span className="muted card-time">{clock(i.at)}</span>
      </Card>
    );
  }
  if (i.kind === 'session') return <SessionCard i={i} />;
  return <TaskLine i={i} titles={titles} />;
}
