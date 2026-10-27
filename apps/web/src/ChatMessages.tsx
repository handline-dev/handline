// One line of the conversation: the person's and the agent's messages with how far each got, and the transcript's tool, prompt, task and
// session lines as small cards. What a line is and where it stands is an icon with its words as the tooltip; a click on a message picks it
// to reply to (the reference the composer shows).
import { memo, useState, type MouseEvent, type ReactNode } from 'react';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { ago, clockTime, ellipsis, isTaskDelivery, landingLabel, openTask, restartCause, sideOf, tickLevel } from '@handline/app-core';
import { useWrite } from '@handline/app-data';
import { useMe } from '@handline/app-react';
import { Button, Card, CardCode, CardWhat, CodeBlock, Dialog, Icon } from '#src/ui/index.ts';
import { bubbleClass, MSG_BODY, MSG_FAILED, MSG_FROM, MSG_META } from '#src/msgClass.ts';
import { Pictures } from '#src/ChatAttach.tsx';
import { Markdown } from '#src/Markdown.tsx';
import { RichText } from '#src/RichText.tsx';
import { useWorkspace } from '#src/Workspace.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';
import { Reveal } from '#src/ui/Reveal.tsx';
import { TaskLine } from '#src/ChatTaskCards.tsx';

/** A plain click on a task link opens the task in the workspace; a modified or middle click keeps the browser's own. */
export const openTaskOnClick = (apply: ReturnType<typeof useWorkspace>['apply'], task: number) => (e: MouseEvent) => {
  if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  apply((w) => openTask(w, task));
};

/** Scrolls the message with this number into view and flashes it; false when it is not drawn (another page, another tab). */
export function jumpTo(id: number): boolean {
  const el = document.querySelector<HTMLElement>(`.msg[data-id="${id}"]`);
  if (!el) return false;
  el.scrollIntoView?.({ block: 'center' });
  el.setAttribute('data-flash', '');
  setTimeout(() => el.removeAttribute('data-flash'), 2000);
  return true;
}

const full = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? undefined : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
};

// A message to a structured agent says how far into the CLI it is; `unknown` is never resent, so the person is told it may have run.
const RECEIPT: Record<string, string> = {
  confirmed: 'Confirmed by the agent',
  unknown: 'May or may not have run',
};
// Queued and sent are the normal course: the tick alone shows them.
const receiptLabel = (state: string) => RECEIPT[state] ?? '';

/** A click on a control inside a bubble is that control's, not a pick of the bubble. */
const onControl = (e: MouseEvent) => !!(e.target as HTMLElement).closest('button, a, input, textarea, form');

/** The stamp hangs in the gutter beside the bubble, out of the flow: an agent's to its right, the person's to its left — the bubble never resizes for it. The time shows on hover; the ticks (`always`) show all the time. */
function Stamp({ left, time, always, hovered }: { left?: boolean; time: ReactNode; always?: ReactNode; hovered?: ReactNode }) {
  return (
    <div
      className={`${MSG_META} absolute bottom-0 flex items-center justify-end whitespace-nowrap gap-1.5 text-ink-3 ${
        left ? 'right-full mr-1.5' : 'left-full ml-1.5'
      }`}
    >
      <span className="hidden items-center gap-1.5 [.peer:hover~*_&]:inline-flex [.peer[aria-current]~*_&]:inline-flex">
        {time}
        {hovered}
      </span>
      {always}
    </div>
  );
}

/** Under an agent's message, shown on hover: copy, quote, and how long ago. Nothing is drawn at rest, but the room stays so the list does not jump. */
function HoverBar({ m, onPick }: { m: ChatMessage; onPick?: (m: ChatMessage) => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(m.body).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <div
      className={`msg-hover -mt-1 flex h-6 items-center gap-1 text-ink-3 opacity-0 transition-opacity focus-within:opacity-100 hover:opacity-100 [.peer:hover+&]:opacity-100 ${MSG_META}`}
    >
      <Button variant="ghost" size="sm" icon={copied ? 'done' : 'copy'} label={copied ? 'Copied' : 'Copy'} onClick={copy} />
      {onPick && m.id >= 0 ? <Button variant="ghost" size="sm" icon="reply" label="Quote" onClick={() => onPick(m)} /> : null}
      <time dateTime={m.at} title={full(m.at)}>
        {ago(m.at)} ago
      </time>
    </div>
  );
}

const tickLabel = (m: ChatMessage) =>
  m.receipt
    ? receiptLabel(m.receipt.state)
    : m.sending_reason
      ? `${landingLabel(m.landing)}: ${m.sending_reason}`
      : landingLabel(m.landing);

/** Two ticks: both grey while unsent, one coloured once sent, both coloured once read. */
function Ticks({ level, label, receipt }: { level: 0 | 1 | 2; label: string; receipt?: string }) {
  return (
    <span
      className="landing receipt inline-flex items-center"
      data-ticks={level}
      data-receipt={receipt}
      title={label || undefined}
      role={label ? 'img' : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
    >
      <Icon name="delivered" size={13} strokeWidth={3} className={level >= 1 ? 'text-ok' : 'text-ink-3'} aria-hidden />
      {/* The front tick has a ring in the chat background, so it reads as lying on the back one. */}
      <span className={`relative -ml-[6px] inline-flex ${level >= 2 ? 'text-ok' : 'text-ink-3'}`}>
        <Icon name="delivered" size={13} strokeWidth={5.5} className="absolute inset-0 text-canvas" aria-hidden />
        <Icon name="delivered" size={13} strokeWidth={3} className="relative" aria-hidden />
      </span>
    </span>
  );
}

/** Under a message of the person's: the task the team took it as (a link), or why it needs none. Turning a message into a request is the lead's job, not the person's. */
function RequestChip({ m }: { m: ChatMessage }) {
  if (m.request_task != null)
    return (
      <a
        className="msg-request mt-0.5 w-fit self-end text-label text-link"
        href={`#/tasks/${m.request_task}`}
        data-request={m.request_task}
      >
        Taken as T{m.request_task}
      </a>
    );
  if (m.no_work) return <span className="msg-request mt-0.5 w-fit self-end text-label text-ink-3">Nothing to do: {m.no_work}</span>;
  return null;
}

export const Message = memo(MessageRow, sameMessage);

type MessageProps = {
  m: ChatMessage;
  agent: string;
  /** The message a reply is being written to. */
  picked?: boolean;
  onPick?: (m: ChatMessage) => void;
  /** The messages the page holds, to say what a reply refers to. */
  byId?: Map<number, ChatMessage>;
  /** What a failed message offers; the chat gives one value for every row. */
  actions?: { retry: (id: number) => void; discard: (id: number) => void };
  /** A message number in the text was clicked. */
  /** In the all-messages room: the sender's name over a message that is not the person's (null when the one before is from the same sender), never `from -> to`. */
  group?: { name: string | null };
  /** The person's newest message: its ticks stay in view, the others' show on hover. */
  latest?: boolean;
};

/** A row draws again only when its own message, its sender line or the message it replies to changed: the chat hands every row the same maps and actions. */
function sameMessage(a: MessageProps, b: MessageProps) {
  const parentOf = (p: MessageProps) => (p.m.parent != null ? p.byId?.get(p.m.parent) : undefined);
  return (
    a.m === b.m &&
    a.agent === b.agent &&
    a.picked === b.picked &&
    a.onPick === b.onPick &&
    a.actions === b.actions &&
    a.group?.name === b.group?.name &&
    a.latest === b.latest &&
    parentOf(a) === parentOf(b)
  );
}

function MessageRow(props: MessageProps) {
  const { m, agent } = props;
  const act = props.actions;
  const side = sideOf(m, agent, useMe());
  const ticks = <Ticks level={tickLevel(m)} label={tickLabel(m)} receipt={m.receipt?.state} />;
  const unnumbered = m.id < 0; // shown before the daemon numbered it (or failed): nothing can be done to it but Retry and Discard
  const failed = m.state === 'failed';
  const parent = m.parent != null ? props.byId?.get(m.parent) : undefined;
  // A click on the message, not on a control in it, picks it to reply to.
  const pick = (e: MouseEvent) => {
    if (unnumbered || failed || !props.onPick || onControl(e)) return;
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
        {props.group && side !== 'person' ? (
          props.group.name ? (
            <div className={`${MSG_FROM} muted`}>{props.group.name}</div>
          ) : null
        ) : side === 'other' ? (
          <div className={`${MSG_FROM} muted`}>
            {isTaskDelivery(m) ? (
              `Task T${m.tasks[0]!.id} handed to ${m.to}`
            ) : (
              <>
                {m.from} <Icon name="forward" size={11} aria-hidden className="inline-block align-[-1px]" /> {m.to}
              </>
            )}
          </div>
        ) : side === 'person' && m.to !== agent ? (
          <div className={`${MSG_FROM} muted`}>to {m.to}</div>
        ) : null}
        {m.parent != null ? (
          <button
            className="msg-ref mb-1 flex w-fit max-w-[16rem] min-w-0 cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-left text-[12px] text-ink-2"
            title={parent ? ellipsis(parent.body, 80) : `#${m.parent}`}
            onClick={() => jumpTo(m.parent!)}
          >
            <Icon name="reference" size={12} label={`Reply to #${m.parent}`} className="shrink-0" />
            {parent ? <span className="min-w-0 truncate">{parent.body.replace(/\s+/g, ' ')}</span> : null}
          </button>
        ) : null}
        {m.parent_item ? (
          <blockquote
            className="msg-quote mx-0 mt-0 mb-1 flex w-fit max-w-[16rem] min-w-0 items-center gap-1 text-[12px] text-ink-2"
            data-item={m.parent_item.id}
          >
            <Icon name="reference" size={12} label="Reply to the agent's reply" className="shrink-0" />
            <span className="min-w-0 truncate">{m.parent_item.text.replace(/\s+/g, ' ')}</span>
          </blockquote>
        ) : null}
        {m.parts?.length ? (
          m.parts.map((p, n) =>
            'text' in p ? (
              p.text ? (
                <div key={n} className={MSG_BODY}>
                  <Markdown text={p.text} agent={agent} />
                </div>
              ) : null
            ) : p.gone ? (
              <span key={n} className="attachment gone muted text-[12px]">
                {p.caption || p.image.name || 'image'} - no longer kept
              </span>
            ) : (
              <Pictures key={n} items={[{ id: p.image.id, name: p.image.name ?? p.caption }]} thumb={side !== 'person'} />
            ),
          )
        ) : (
          <>
            {m.body ? (
              <div className={MSG_BODY}>
                <Markdown text={m.body} agent={agent} />
              </div>
            ) : null}
            {m.attachments?.length ? <Pictures items={m.attachments} thumb={side !== 'person'} /> : null}
          </>
        )}
        {m.receipt?.state === 'unknown' ? (
          <div className={MSG_FAILED} role="note">
            <Icon name="maybeRan" size={13} /> This message may or may not have run. It is not sent again; send it again yourself if it
            matters.
          </div>
        ) : null}
        {failed ? (
          <div className={MSG_FAILED} role="alert">
            <Icon name="failed" size={13} /> {m.sending_reason}{' '}
            <button className="icon" aria-label="Retry" title="Retry" onClick={() => act?.retry(m.id)}>
              <Icon name="retry" size={14} />
            </button>
            <button className="icon" aria-label="Discard" title="Discard" onClick={() => act?.discard(m.id)}>
              <Icon name="discard" size={14} />
            </button>
          </div>
        ) : null}
      </div>
      {side === 'person' && !unnumbered && !failed ? <RequestChip m={m} /> : null}
      {side === 'person' ? (
        <Stamp
          left
          time={clockTime(m.at)}
          always={props.latest && !failed ? ticks : null}
          hovered={props.latest || failed ? null : ticks}
        />
      ) : unnumbered || failed ? null : (
        <HoverBar m={m} onPick={props.onPick} />
      )}
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
      <Reveal show={open} className="basis-full">
        <CodeBlock className="tool-output max-h-[20lh] overflow-auto max-sm:whitespace-pre-wrap">{i.output}</CodeBlock>
      </Reveal>
      {cut && (open || !i.output) ? (
        <span className="muted">
          {cut} more line{cut === 1 ? '' : 's'} let go
        </span>
      ) : null}
    </div>
  );
}

/** A restart is one centered line: when the new session began and why, and the handoff note it began from in a dialog. */
function SessionCard({ i }: { i: TranscriptItem }) {
  const [open, setOpen] = useState(false);
  const cause = restartCause(i);
  const handoff = i.session?.handoff || null;
  return (
    <div className="card-session group flex items-center gap-3 text-meta text-muted" data-kind="session">
      <hr className="m-0 h-px flex-1 border-0 bg-line" />
      <span>
        New session<span className="card-time hidden group-hover:inline"> at {clockTime(i.at)}</span>
        {cause ? `: ${cause}` : ''}
      </span>
      {handoff ? (
        <Button variant="outline" size="sm" icon="note" label="Handoff note" onClick={() => setOpen(true)}>
          Handoff note
        </Button>
      ) : null}
      <hr className="m-0 h-px flex-1 border-0 bg-line" />
      <AnimatedList>
        {open ? (
          <Dialog key="handoff" title="Handoff note" onClose={() => setOpen(false)}>
            <CodeBlock className="handoff-full max-h-[60vh] overflow-auto whitespace-pre-wrap">{handoff}</CodeBlock>
          </Dialog>
        ) : null}
      </AnimatedList>
    </div>
  );
}

/** A batch discard as one card: a line per task with its title and reason; the line opens the task, Reopen brings it back as a draft. */
function BatchCard({ i }: { i: TranscriptItem }) {
  const { apply } = useWorkspace();
  const reopen = useWrite('task.reopen');
  const b = i.task_batch;
  if (!b) return null;
  return (
    <Card variant="chat" className="card-batch flex-col items-stretch" data-kind="task_batch">
      <div className="flex items-baseline gap-x-2">
        <Icon name="withdrawn" size={14} label="Discarded" />
        <span className="card-event text-meta font-semibold uppercase tracking-wide">Discarded {b.tasks.length}</span>
        <span className="muted card-time ml-auto">{clockTime(i.at)}</span>
      </div>
      {b.tasks.map((t) => (
        <div key={t.task} className="flex items-baseline gap-x-2" data-task={t.task}>
          <button
            type="button"
            className="flex min-w-0 flex-1 cursor-pointer flex-wrap items-baseline gap-x-2 border-0 bg-transparent p-0 text-left text-body"
            title={`Open T${t.task} in the Tasks tab`}
            onClick={() => apply((w) => openTask(w, t.task))}
          >
            <span className="mono">T{t.task}</span>
            <span className="max-w-[28ch] overflow-hidden text-ellipsis whitespace-nowrap font-medium">{t.title}</span>
            <span className="muted min-w-0 break-words">{t.reason}</span>
          </button>
          <Button label={`Reopen T${t.task}`} onClick={() => reopen.mutate({ task: t.task, note: 'reopened from the batch card' })}>
            Reopen
          </Button>
        </div>
      ))}
    </Card>
  );
}

/** A transcript line that is not a message: a reply, tool, prompt, session or task line. */
export function Item({
  i,
  titles,
  agent,
  onReply,
  picked,
}: {
  i: TranscriptItem;
  titles?: Map<number, string>;
  /** The agent whose chat this is: a task card by someone else names who acted. */
  agent?: string;
  onReply?: (i: TranscriptItem) => void;
  /** The reply is being written to this item. */
  picked?: boolean;
}) {
  if (i.kind === 'reply')
    return (
      <>
        <div
          className={bubbleClass('agent', { picked, pickable: !!onReply })}
          data-kind="reply"
          data-id={i.id}
          title={full(i.at)}
          aria-current={picked ? 'true' : undefined}
          onClick={(e) => {
            if (onReply && !onControl(e)) onReply(i);
          }}
        >
          <div className={MSG_BODY}>
            <RichText text={i.text ?? ''} />
          </div>
        </div>
        <Stamp time={clockTime(i.at)} />
      </>
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
        <span className="card-state" title={i.open ? 'Needs you' : 'Answered'}>
          <Icon name={i.open ? 'waiting' : 'answered'} size={14} label={i.open ? 'Needs you' : 'Answered'} />
        </span>
        <span className="muted card-time">{clockTime(i.at)}</span>
      </Card>
    );
  }
  if (i.kind === 'session') return <SessionCard i={i} />;
  if (i.kind === 'task_batch') return <BatchCard i={i} />;
  return <TaskLine i={i} titles={titles} agent={agent} />;
}
