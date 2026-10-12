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

/** The name the daemon records for the person's own words: the CLI's `human`, or the device they typed on (`me`, e.g. "Handline desktop"). */
export const isPerson = (name: string, me: string) => name === 'human' || (!!me && name === me);
/** Who a message is from, for the agent whose chat this is: the person, the agent, or someone else (another agent, the task board: the *all* tab, notices). Only the person's own words sit on the person's side. */
export function sideOf(m: ChatMessage, agent: string, me: string): 'person' | 'agent' | 'other' {
  if (m.from === agent) return 'agent';
  return isPerson(m.from, me) ? 'person' : 'other';
}
/** An agent's question that still waits for the person: the daemon keeps it queued until answered, so a report or reply that asked nothing is not one. */
export const isOpenAsk = (m: ChatMessage, agent: string) =>
  m.from === agent && m.state === 'queued' && !m.answer && !m.dismissed && m.landing !== 'withdrawn';
/** Not yet in the agent's terminal, so it can still be taken back. */
export const canWithdraw = (m: ChatMessage, agent: string) =>
  m.to === agent && m.from !== agent && (m.landing === 'queued' || m.landing === 'typed');

const waiting = (m: ChatMessage) => (m.receipt ? m.receipt.state === 'queued' : m.landing === 'queued');
/** How many of the person's earlier messages to this agent are still queued ahead of this one. */
export const behindCount = (messages: ChatMessage[], m: ChatMessage): number =>
  messages.filter((o) => o.id > 0 && o.id < m.id && o.to === m.to && o.from === m.from && waiting(o)).length;
/** The short line under the person's own message while it has not reached the agent; null once it has (and for a failed one, which says so itself). */
export function sendNote(m: ChatMessage, behind: number): string | null {
  if (m.state === 'failed') return null;
  if (m.id < 0) return 'Sending…';
  if (waiting(m)) return behind ? `Waiting behind ${behind}` : 'Queued';
  if (m.receipt) return m.receipt.state === 'sent' ? 'Sent, not confirmed yet' : null;
  return m.landing === 'typed' ? 'Typed, not read yet' : null;
}

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
