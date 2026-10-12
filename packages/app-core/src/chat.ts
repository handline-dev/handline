// The pure side of chat: how messages and transcript items are ordered, who a message is from, and what can still be done to it.
// Fetching and keeping the conversation current is the apps' shared query layer (@handline/app-data).
import type { ChatMessage, Landing, TranscriptItem } from '@handline/client';

export type TimelineEntry =
  { kind: 'message'; at: string; id: number; message: ChatMessage } | { kind: 'item'; at: string; id: number; item: TranscriptItem };

/** Everything on screen for one agent, oldest first: the messages and the agent's transcript by time. */
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
/** An agent's question that still waits for the person: the daemon keeps it queued until answered, so a report or reply that asked nothing is not one. */
export const isOpenAsk = (m: ChatMessage, agent: string) =>
  m.from === agent && m.state === 'queued' && !m.answer && !m.dismissed && m.landing !== 'withdrawn';
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

const TASK_LABEL: Record<string, string> = { opened: 'Task added', changed: 'Task edited', done: 'Task done', withdrawn: 'Task deleted' };
/** What a task line in the chat shows: the label (added / edited / done / deleted), the task, and only what changed, briefly. */
export interface TaskRow {
  label: string;
  task: number;
  title: string | null;
  /** The change (`priority 2 -> 1`) or the evidence (done note, deletion reason); null when there is none. */
  detail: string | null;
}
export function taskRow(i: TranscriptItem, titles?: Map<number, string>): TaskRow {
  const e = i.task_event;
  const ev = e?.event ?? 'changed';
  const change = (e as { change?: string | null } | undefined)?.change;
  return {
    label: TASK_LABEL[ev] ?? 'Task edited',
    task: e?.task ?? NaN,
    title: titles?.get(e?.task ?? NaN) ?? null,
    detail: change || e?.evidence || null,
  };
}
