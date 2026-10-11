// One Inbox item as a card: the kind's body and the verbs the daemon serves for it. A verb the daemon does not serve is shown disabled
// with the reason; an authored write shows its new state at once and a muted "sending" mark until the daemon settles it.
import { useState, type MouseEvent } from 'react';
import { ago } from '@handline/app-core';
import type { InboxItem } from '@handline/client';
import { Button, Icon, isIcon, type IconName } from './ui/index.ts';

export const KIND_LABEL: Record<string, string> = {
  decision: 'Decision',
  ask: 'Question',
  say: 'Message',
  report: 'Result',
  result: 'Result',
  block: 'Block',
  prompt: 'Prompt',
};
export const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind;
/** The icon of a kind: `report` is a result; a kind the app has no icon for falls back to the message one. */
export const kindIcon = (kind: string): IconName => {
  const k = kind === 'report' ? 'result' : kind;
  return isIcon(k) ? k : 'say';
};

/** The verbs the page offers a card, each with the reason it is off (or null). */
export interface Verbs {
  read: (id: number) => void;
  dismiss: (id: number) => void;
  answer: (id: number, how: { option: string } | { text: string }) => void;
  reply: (id: number, text: string) => void;
  /** Why a verb is off: "daemon not connected", "this daemon does not serve inbox.reply"; null when it works. */
  off: Record<'read' | 'dismiss' | 'answer' | 'reply', string | null>;
}

const EVIDENCE_LINES = 3;
export const answerable = (i: InboxItem) => i.kind === 'decision' || i.kind === 'ask';

/** Where a click on a card goes: the task it is about, else the agent's chat. */
export const originOf = (i: InboxItem): string | null =>
  i.task != null ? `#/tasks/${i.task}` : i.agent ? `#/agents/${encodeURIComponent(i.agent)}` : null;

function TextBox(props: {
  label: string;
  submit: string;
  icon: IconName;
  off: string | null;
  onSend: (text: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState('');
  return (
    <form
      className="inbox-box"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim() && !props.off) props.onSend(text.trim());
      }}
    >
      <textarea
        data-answer
        autoFocus
        rows={2}
        aria-label={props.label}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') props.onCancel();
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit();
        }}
      />
      <button
        className="icon"
        type="submit"
        aria-label={props.submit}
        disabled={!text.trim() || !!props.off}
        title={props.off ?? props.submit}
      >
        <Icon name={props.icon} label={props.submit} />
      </button>
      <button className="icon" type="button" aria-label="Cancel" title="Cancel" onClick={props.onCancel}>
        <Icon name="close" label="Cancel" />
      </button>
    </form>
  );
}

/** An item that is on the page: a question that waits for its answer stays after it is read, anything else leaves when read. */
export const waits = (i: InboxItem, readPending = false): boolean => (answerable(i) ? !i.answered : !i.read && !readPending);

/** What opening an item does, by click or by Enter: mark it read, then go to its task or chat. */
export function open(i: InboxItem, v: Verbs): void {
  if (!i.read && !v.off.read) v.read(i.id);
  const to = originOf(i);
  if (to) location.hash = to;
}

export function InboxCard(props: { item: InboxItem; on: boolean; pending: boolean; verbs: Verbs }) {
  const { item: i, verbs: v } = props;
  const [box, setBox] = useState<'answer' | 'reply' | null>(null);
  const [whole, setWhole] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const lines = (i.evidence ?? '').split('\n');
  const origin = originOf(i);
  const done = (send: () => void) => {
    send();
    setBox(null);
  };
  // A click on the card itself reads it and goes to its chat or task; a click on a control inside it does only that control's job.
  const go = (e: MouseEvent) => {
    if ((e.target as HTMLElement).closest('button, a, textarea, input, pre, form')) return;
    open(i, v);
  };
  const more = expanded || props.on;
  return (
    <article
      className={`inbox-card${i.read ? ' read' : ''}${props.on ? ' on' : ''}${origin ? ' go' : ''}${more ? ' more' : ''}`}
      data-item={i.id}
      data-kind={i.kind}
      data-pending={props.pending ? '' : undefined}
      aria-current={props.on ? 'true' : undefined}
      onClick={go}
    >
      <header className="inbox-row">
        <span className="inbox-seen" title={i.read ? 'Read' : 'Unread'}>
          <Icon name={i.read ? 'read' : 'unread'} size={14} label={i.read ? 'Read' : 'Unread'} />
        </span>
        <span className="badge mr-1.5 inline-block rounded-card border border-line px-1.5 text-hint text-ink-2" title={kindLabel(i.kind)}>
          <Icon name={kindIcon(i.kind)} label={kindLabel(i.kind)} />
        </span>
        <h3 className="inbox-title">{i.title}</h3>
        {i.agent ? <span className="inbox-who mono">{i.agent}</span> : null}
        {i.task != null ? <span className="inbox-who mono">T{i.task}</span> : null}
        <span className="inbox-age muted">{ago(i.created)}</span>
        <span className="inbox-hover">
          <Button
            size="sm"
            icon={more ? 'less' : 'more'}
            label={more ? 'Hide details' : 'Show details'}
            onClick={() => setExpanded(!more)}
          />
          {!i.read ? (
            <Button
              size="sm"
              icon="markRead"
              label="Mark read"
              disabled={!!v.off.read}
              title={v.off.read ?? 'Mark read'}
              onClick={() => v.read(i.id)}
            />
          ) : null}
          <Button
            size="sm"
            icon="dismiss"
            label="Dismiss"
            disabled={!!v.off.dismiss}
            title={v.off.dismiss ?? 'Dismiss'}
            onClick={() => v.dismiss(i.id)}
          />
        </span>
      </header>
      {more ? (
        <div className="inbox-detail">
          {i.body && i.body !== i.title ? <p className="inbox-body">{i.body}</p> : null}
          {i.evidence ? (
            <div>
              <pre className="lines">{(whole ? lines : lines.slice(0, EVIDENCE_LINES)).join('\n')}</pre>
              {lines.length > EVIDENCE_LINES ? (
                <Button
                  size="sm"
                  icon={whole ? 'less' : 'more'}
                  label={whole ? 'Show less' : 'Show more'}
                  onClick={() => setWhole(!whole)}
                />
              ) : null}
            </div>
          ) : null}
          {i.prompt ? (
            // The prompt's own card (permission, dialog) is the prompt-card unit's; here the item shows what is asked and where to answer.
            <pre className="lines" data-prompt-slot>
              {i.prompt.command ?? i.prompt.lines.join('\n')}
            </pre>
          ) : null}
          {props.pending ? (
            <p className="muted inbox-mark" role="status">
              <Icon name="sending" />
            </p>
          ) : null}
          {i.answered ? (
            <p className="muted">
              <Icon name="answer" label="Answered" />
            </p>
          ) : null}
          <div className="inbox-actions">
            {answerable(i) && !i.answered
              ? i.options.map((o) => (
                  <Button
                    key={o.id}
                    variant="outline"
                    label={o.label}
                    disabled={!!v.off.answer}
                    title={v.off.answer ?? undefined}
                    onClick={() => v.answer(i.id, { option: o.id })}
                  />
                ))
              : null}
            {answerable(i) && !i.answered ? (
              <Button
                icon="answer"
                label="Answer"
                disabled={!!v.off.answer}
                title={v.off.answer ?? 'Answer'}
                data-verb="answer"
                onClick={() => setBox('answer')}
              />
            ) : null}
            {!answerable(i) && !i.prompt && i.kind !== 'block' ? (
              <Button
                icon="reply"
                label="Reply"
                disabled={!!v.off.reply}
                title={v.off.reply ?? 'Reply'}
                data-verb="reply"
                onClick={() => setBox('reply')}
              />
            ) : null}
          </div>
        </div>
      ) : null}
      {box === 'answer' ? (
        <TextBox
          label="Your answer"
          submit="Answer"
          icon="answer"
          off={v.off.answer}
          onCancel={() => setBox(null)}
          onSend={(t) => done(() => v.answer(i.id, { text: t }))}
        />
      ) : null}
      {box === 'reply' ? (
        <TextBox
          label="Your reply"
          submit="Send reply"
          icon="reply"
          off={v.off.reply}
          onCancel={() => setBox(null)}
          onSend={(t) => done(() => v.reply(i.id, t))}
        />
      ) : null}
    </article>
  );
}
