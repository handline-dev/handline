// The pure side of chat: how messages and transcript items are ordered, who a message is from, and what can still be done to it.
// Fetching and keeping the conversation current is the apps' shared query layer (@handline/app-data).
import type { ChatMessage, Landing, TranscriptItem } from '@handline/client';

export type TimelineEntry =
  { kind: 'message'; at: string; id: number; message: ChatMessage } | { kind: 'item'; at: string; id: number; item: TranscriptItem };

/** Everything on screen for one agent, oldest first: the person's messages and the agent's transcript by time. */
export function timeline(messages: ChatMessage[], items: TranscriptItem[]): TimelineEntry[] {
  const out: TimelineEntry[] = [
    ...messages.map((m): TimelineEntry => ({ kind: 'message', at: m.at, id: m.id, message: m })),
    ...items.map((i): TimelineEntry => ({ kind: 'item', at: i.at, id: i.id, item: i })),
  ];
  return out.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : a.kind === b.kind ? a.id - b.id : a.kind === 'message' ? -1 : 1));
}

/** Who a message is from, for the agent whose chat this is: the person, the agent, or another agent (the *all* tab). */
export function sideOf(m: ChatMessage, agent: string, isAgent: (name: string) => boolean): 'person' | 'agent' | 'other' {
  if (m.from === agent) return 'agent';
  if (m.to === agent && !isAgent(m.from)) return 'person';
  return 'other';
}
/** An agent's question that still waits for the person. */
export const isOpenAsk = (m: ChatMessage, agent: string) => m.from === agent && !m.answer && !m.dismissed && m.landing !== 'withdrawn';
/** Not yet in the agent's terminal, so it can still be taken back. */
export const canWithdraw = (m: ChatMessage, agent: string) =>
  m.to === agent && m.from !== agent && (m.landing === 'queued' || m.landing === 'typed');

const LABELS: Record<string, string> = {
  queued: 'Sending',
  typed: 'Typed',
  delivered: 'Delivered',
  delivered_unanswered: 'Not answered',
  answered: 'Answered',
  dismissed: 'Dismissed',
  withdrawn: 'Withdrawn',
};
export const landingLabel = (l: Landing): string => LABELS[l] ?? l;
