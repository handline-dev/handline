// One chat line, redrawn only when its own subject changed.
import { memo } from 'react';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import type { TaskFold, TimelineEntry } from '@handline/app-core';
import { AnimatedItem } from '#src/AnimatedList.tsx';
import { FoldLine, type useFolds } from '#src/ChatFold.tsx';
import { Item, Message } from '#src/ChatMessages.tsx';
import { TaskNote } from '#src/ChatTaskCards.tsx';
import { DecidedCard } from '#src/ChatCards.tsx';

type Props = {
  e: TimelineEntry | TaskFold;
  instant: boolean;
  agent: string;
  /** This line is the message or the reply being answered. */
  picked: boolean;
  group?: { name: string | null };
  /** The person's newest message: it keeps its ticks in view. */
  latest?: boolean;
  onPick: (m: ChatMessage, quote?: string) => void;
  onReply?: (i: TranscriptItem) => void;
  byId: Map<number, ChatMessage>;
  actions: { retry: (id: number) => void; discard: (id: number) => void };
  titles: Map<number, string>;
  folds: Omit<ReturnType<typeof useFolds>, 'entries'>;
};

/** The entry is rebuilt on every change; the object inside it is not. */
const subject = (e: Props['e']) =>
  e.kind === 'fold' ? e : e.kind === 'message' || e.kind === 'note' ? e.message : e.kind === 'item' ? e.item : e.proposal;

function same(a: Props, b: Props) {
  const parentOf = (p: Props) => (p.e.kind === 'message' && p.e.message.parent != null ? p.byId.get(p.e.message.parent) : undefined);
  return (
    a.e.kind === b.e.kind &&
    a.e.kind !== 'fold' &&
    subject(a.e) === subject(b.e) &&
    a.instant === b.instant &&
    a.agent === b.agent &&
    a.picked === b.picked &&
    a.group?.name === b.group?.name &&
    a.latest === b.latest &&
    a.onPick === b.onPick &&
    !a.onReply === !b.onReply &&
    parentOf(a) === parentOf(b) &&
    a.actions === b.actions &&
    a.titles === b.titles
  );
}

export const ChatRow = memo(function ChatRow({
  e,
  instant,
  agent,
  picked,
  group,
  latest,
  onPick,
  onReply,
  byId,
  actions,
  titles,
  folds,
}: Props) {
  return (
    <AnimatedItem
      instant={instant}
      className="max-w-full has-[>_.msg]:relative has-[>_.msg-hover]:-mb-2 has-[>_.msg.person]:max-w-[70%] has-[>_.msg.person]:self-end has-[>_.msg:not(.person)]:self-start has-[>_.card-task]:self-stretch"
    >
      {e.kind === 'fold' ? (
        <FoldLine f={e} folds={folds} titles={titles} agent={agent} />
      ) : e.kind === 'message' ? (
        <Message m={e.message} agent={agent} picked={picked} onPick={onPick} byId={byId} actions={actions} group={group} latest={latest} />
      ) : e.kind === 'proposal' ? (
        <DecidedCard p={e.proposal} />
      ) : e.kind === 'note' ? (
        <TaskNote m={e.message} task={e.task} titles={titles} />
      ) : (
        <Item i={e.item} titles={titles} agent={agent} onReply={onReply} picked={picked} />
      )}
    </AnimatedItem>
  );
}, same);
