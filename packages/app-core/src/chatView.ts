// What a chat draws. The messages and the agent's transcript are paged separately, so a page of one can reach back further than the other's;
// only the stretch both have read is drawn, otherwise the head of the chat would show one kind of line alone.
import type { ChatMessage, Proposal, TranscriptItem } from '@handline/client';
import { timeline, type TimelineEntry } from '#src/chat.ts';

export interface ChatSource {
  messages: readonly ChatMessage[];
  items: readonly TranscriptItem[];
  /** The daemon has older lines of this kind than the oldest read. */
  moreMessages: boolean;
  moreItems: boolean;
}

/** The time before which a source has not been read: its oldest line, when it has older ones; null when it was read to the start. */
const readFrom = (lines: readonly { at: string }[], more: boolean): string | null =>
  more && lines.length ? lines.reduce((a, l) => (l.at < a ? l.at : a), lines[0]!.at) : null;

/** The coverage watermark: the oldest time both sources have read, or null when both reached the start of the chat. */
export function coverage(s: ChatSource): string | null {
  const [a, b] = [readFrom(s.messages, s.moreMessages), readFrom(s.items, s.moreItems)];
  return a !== null && b !== null ? (a > b ? a : b) : (a ?? b);
}

/** The entries to draw, oldest first, and whether older ones are still to be read above them. */
export function chatView(
  s: ChatSource,
  scope: 'mine' | 'all',
  agent: string,
  me: string,
  decided: readonly Proposal[] = [],
): { entries: TimelineEntry[]; floor: string | null; older: boolean } {
  const floor = coverage(s);
  const drawn = (at: string) => floor === null || at >= floor;
  const entries = timeline(
    s.messages.filter((m) => drawn(m.at)),
    s.items.filter((i) => drawn(i.at)),
    scope,
    agent,
    me,
    decided.filter((p) => p.settled && drawn(p.settled)),
  );
  return { entries, floor, older: s.moreMessages || s.moreItems };
}
