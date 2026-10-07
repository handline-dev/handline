// One Inbox item as a card: the kind's body and the verbs the daemon serves for it. A verb the daemon does not serve is shown disabled
// with the reason; an authored write shows its new state at once and a muted "sending" mark until the daemon settles it.
import { useState } from 'react';
import { ago } from '@handline/app-core';
import type { InboxItem } from '@handline/client';

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
const answerable = (i: InboxItem) => i.kind === 'decision' || i.kind === 'ask';

/** Where a card's "Open" goes: the task it is about, else the agent's chat. */
export const originOf = (i: InboxItem): string | null =>
  i.task != null ? `#/tasks/${i.task}` : i.agent ? `#/agents/${encodeURIComponent(i.agent)}` : null;

function TextBox(props: { label: string; submit: string; off: string | null; onSend: (text: string) => void; onCancel: () => void }) {
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
      <button className="btn primary" type="submit" disabled={!text.trim() || !!props.off} title={props.off ?? undefined}>
        {props.submit}
      </button>
      <button className="btn" type="button" onClick={props.onCancel}>
        Cancel
      </button>
    </form>
  );
}

export function InboxCard(props: { item: InboxItem; on: boolean; pending: boolean; verbs: Verbs }) {
  const { item: i, verbs: v } = props;
  const [box, setBox] = useState<'answer' | 'reply' | null>(null);
  const [whole, setWhole] = useState(false);
  const lines = (i.evidence ?? '').split('\n');
  const origin = originOf(i);
  const done = (send: () => void) => {
    send();
    setBox(null);
  };
  return (
    <article
      className={`inbox-card${i.read ? ' read' : ''}${props.on ? ' on' : ''}`}
      data-item={i.id}
      data-kind={i.kind}
      data-pending={props.pending ? '' : undefined}
      aria-current={props.on ? 'true' : undefined}
    >
      <header className="inbox-head">
        <span className="inbox-dot" aria-label={i.read ? 'read' : 'unread'}>
          {i.read ? '○' : '●'}
        </span>
        <span className="badge">{kindLabel(i.kind)}</span>
        {i.agent ? <span className="muted mono">{i.agent}</span> : null}
        <span className="muted inbox-age">{ago(i.created)}</span>
      </header>
      <h3 className="inbox-title">{i.title}</h3>
      {i.body && i.body !== i.title ? <p className="inbox-body">{i.body}</p> : null}
      {i.evidence ? (
        <div>
          <pre className="lines">{(whole ? lines : lines.slice(0, EVIDENCE_LINES)).join('\n')}</pre>
          {lines.length > EVIDENCE_LINES ? (
            <button className="btn" onClick={() => setWhole(!whole)}>
              {whole ? 'Show less' : 'Read whole'}
            </button>
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
          <i>○ sending…</i>
        </p>
      ) : null}
      {i.answered ? <p className="muted">Answered</p> : null}
      <div className="card-actions inbox-actions">
        {answerable(i) && !i.answered
          ? i.options.map((o) => (
              <button
                key={o.id}
                className="btn"
                disabled={!!v.off.answer}
                title={v.off.answer ?? undefined}
                onClick={() => v.answer(i.id, { option: o.id })}
              >
                {o.label}
              </button>
            ))
          : null}
        {answerable(i) && !i.answered ? (
          <button className="btn" disabled={!!v.off.answer} title={v.off.answer ?? undefined} onClick={() => setBox('answer')}>
            Answer…
          </button>
        ) : null}
        {!answerable(i) && !i.prompt && i.kind !== 'block' ? (
          <button className="btn" disabled={!!v.off.reply} title={v.off.reply ?? undefined} onClick={() => setBox('reply')}>
            Reply…
          </button>
        ) : null}
        {origin ? (
          <a className="btn" href={origin}>
            {i.task != null ? 'Open task' : 'Open chat'}
          </a>
        ) : null}
        <span className="grow" />
        {!i.read ? (
          <button className="btn" disabled={!!v.off.read} title={v.off.read ?? undefined} onClick={() => v.read(i.id)}>
            Mark read
          </button>
        ) : null}
        <button className="btn" disabled={!!v.off.dismiss} title={v.off.dismiss ?? undefined} onClick={() => v.dismiss(i.id)}>
          Dismiss
        </button>
      </div>
      {box === 'answer' ? (
        <TextBox
          label="Your answer"
          submit="Answer"
          off={v.off.answer}
          onCancel={() => setBox(null)}
          onSend={(t) => done(() => v.answer(i.id, { text: t }))}
        />
      ) : null}
      {box === 'reply' ? (
        <TextBox
          label="Your reply"
          submit="Send reply"
          off={v.off.reply}
          onCancel={() => setBox(null)}
          onSend={(t) => done(() => v.reply(i.id, t))}
        />
      ) : null}
    </article>
  );
}
