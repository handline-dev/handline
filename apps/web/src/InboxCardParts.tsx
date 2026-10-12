// The parts of an Inbox card: the row (mark, kind, title, who, age, the verbs that show on hover), the detail under it and the answer box.
import { useState } from 'react';
import { ago } from '@handline/app-core';
import type { InboxItem } from '@handline/client';
import { Button, CodeBlock, Icon, type IconName } from './ui/index.ts';
import { answerable, kindIcon, kindLabel, type Verbs } from './inboxKinds.ts';

export const EVIDENCE_LINES = 3;

export function TextBox(props: {
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
      className="inbox-box mt-2 flex flex-wrap gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim() && !props.off) props.onSend(text.trim());
      }}
    >
      <textarea
        className="flex-[1_1_100%] rounded-ctl border border-line bg-canvas px-2 py-1.5 text-ink"
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

// Read dims to the second ink level, not lower; the filled and hollow marks keep the difference by shape.
const TITLE =
  'inbox-title mt-1.5 mb-1 min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap font-sans text-[15px]/[18px] [overflow-wrap:anywhere]';
// On touch there is no hover: the verbs of a row are always there.
const VERBS =
  'inbox-hover inline-flex gap-0.5 opacity-0 [@media(hover:none)]:opacity-100 group-hover/card:opacity-100 group-focus-within/card:opacity-100 group-[.on]/card:opacity-100 group-[.more]/card:opacity-100';

export function InboxRow(props: { item: InboxItem; verbs: Verbs; more: boolean; onToggle: () => void }) {
  const { item: i, verbs: v, more } = props;
  return (
    <header className="inbox-row flex min-h-13 min-w-0 items-center gap-2 px-2">
      <span className={`inbox-seen inline-flex ${i.read ? 'text-ink-2' : 'text-ink'}`} title={i.read ? 'Read' : 'Unread'}>
        <Icon name={i.read ? 'read' : 'unread'} size={14} label={i.read ? 'Read' : 'Unread'} />
      </span>
      <span className="badge mr-1.5 inline-flex items-end gap-px border-0 p-0 text-hint text-ink-2" title={kindLabel(i.kind)}>
        <Icon name={kindIcon(i.kind)} label={kindLabel(i.kind)} />
      </span>
      <h3 className={`${TITLE} ${i.read ? 'font-normal text-ink-2' : 'font-medium'}`}>{i.title}</h3>
      {i.agent ? <span className="inbox-who mono whitespace-nowrap text-ink-2">{i.agent}</span> : null}
      {i.task != null ? <span className="inbox-who mono whitespace-nowrap text-ink-2">T{i.task}</span> : null}
      <span className="inbox-age muted ml-auto whitespace-nowrap">{ago(i.created)}</span>
      <span className={VERBS}>
        <Button size="sm" icon={more ? 'less' : 'more'} label={more ? 'Hide details' : 'Show details'} onClick={props.onToggle} />
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
  );
}

export function InboxDetail(props: {
  item: InboxItem;
  verbs: Verbs;
  pending: boolean;
  whole: boolean;
  onWhole: () => void;
  onBox: (box: 'answer' | 'reply') => void;
}) {
  const { item: i, verbs: v } = props;
  const lines = (i.evidence ?? '').split('\n');
  return (
    <div className="inbox-detail pr-2 pb-2 pl-8">
      {i.body && i.body !== i.title ? (
        <p className="inbox-body m-0 mb-1.5 font-normal text-ink-2 [overflow-wrap:anywhere]">{i.body}</p>
      ) : null}
      {i.evidence ? (
        <div>
          <CodeBlock scroll>{(props.whole ? lines : lines.slice(0, EVIDENCE_LINES)).join('\n')}</CodeBlock>
          {lines.length > EVIDENCE_LINES ? (
            <Button
              size="sm"
              icon={props.whole ? 'less' : 'more'}
              label={props.whole ? 'Show less' : 'Show more'}
              onClick={props.onWhole}
            />
          ) : null}
        </div>
      ) : null}
      {i.prompt ? (
        // The prompt's own card (permission, dialog) is the prompt-card unit's; here the item shows what is asked and where to answer.
        <CodeBlock scroll data-prompt-slot>{i.prompt.command ?? i.prompt.lines.join('\n')}</CodeBlock>
      ) : null}
      {props.pending ? (
        <p className="muted inbox-mark my-1" role="status">
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
            onClick={() => props.onBox('answer')}
          />
        ) : null}
        {!answerable(i) && !i.prompt && i.kind !== 'block' ? (
          <Button
            icon="reply"
            label="Reply"
            disabled={!!v.off.reply}
            title={v.off.reply ?? 'Reply'}
            data-verb="reply"
            onClick={() => props.onBox('reply')}
          />
        ) : null}
      </div>
    </div>
  );
}
