// One Inbox item as a card: the kind's body and the verbs the daemon serves for it. A verb the daemon does not serve is shown disabled
// with the reason; an authored write shows its new state at once and a muted "sending" mark until the daemon settles it.
import { useState, type MouseEvent } from 'react';
import type { InboxItem } from '@handline/client';
import { InboxDetail, InboxRow, TextBox } from './InboxCardParts.tsx';
import { cardClass, originOf, type Verbs } from './inboxKinds.ts';

/** An item that is on the page: it leaves when read, and so does one whose mark-read is still on its way. */
export const waits = (i: InboxItem, readPending = false): boolean => !i.read && !readPending;

/** What opening an item does, by click or by Enter: mark it read, then go to its task or chat. */
export function open(i: InboxItem, v: Verbs): void {
  if (!i.read && !v.off.read) v.read(i.id);
  const to = originOf(i);
  if (to) location.hash = to;
}

export function InboxCard(props: { item: InboxItem; on: boolean; pending: boolean; verbs: Verbs }) {
  const { item: i, verbs: v } = props;
  const [box, setBox] = useState<'reply' | null>(null);
  const [whole, setWhole] = useState(false);
  const [expanded, setExpanded] = useState(false);
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
      className={cardClass({ read: i.read, on: props.on, go: originOf(i) != null, more })}
      data-item={i.id}
      data-kind={i.kind}
      data-pending={props.pending ? '' : undefined}
      aria-current={props.on ? 'true' : undefined}
      onClick={go}
    >
      <InboxRow item={i} verbs={v} more={more} onToggle={() => setExpanded(!more)} />
      {more ? (
        <InboxDetail item={i} verbs={v} pending={props.pending} whole={whole} onWhole={() => setWhole(!whole)} onBox={setBox} />
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
