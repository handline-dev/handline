// The pure side of chat: how messages and transcript items are ordered, who a message is from, and what can still be done to it.
// Fetching and keeping the conversation current is the apps' shared query layer (@handline/app-data).
import type { ChatMessage, Landing, TranscriptItem } from '@handline/client';

export type TimelineEntry =
  | { kind: 'message'; at: string; id: number; message: ChatMessage }
  /** The task board's word about a task (`T<N> done by x: ...`), drawn as a task card, not a bubble. */
  | { kind: 'note'; at: string; id: number; message: ChatMessage; task: number }
  | { kind: 'item'; at: string; id: number; item: TranscriptItem };

/** The sender of the daemon's own notices; its words are never the person's. */
export const NOTICE_SENDER = 'task-board';
/** What a task-board message is: a task's news (`T<N> done by ...`), or a delivery notice (`#<N> to x was written ...`) that only concerns the sender. */
export function noticeOf(m: ChatMessage): { kind: 'task'; task: number } | { kind: 'delivery' } | null {
  if (m.from !== NOTICE_SENDER) return null;
  const task = /^T(\d+)\s/.exec(m.body);
  if (task) return { kind: 'task', task: Number(task[1]) };
  return /^#\d+\s/.test(m.body) ? { kind: 'delivery' } : null;
}

/** Everything on screen for one agent, oldest first: the messages and the agent's transcript by time. In the agent's own chat (`mine`) the task board's delivery notices are left out. */
export function timeline(messages: ChatMessage[], items: TranscriptItem[], scope: 'mine' | 'all' = 'mine'): TimelineEntry[] {
  const out: TimelineEntry[] = [
    ...messages.flatMap((m): TimelineEntry[] => {
      const n = noticeOf(m);
      if (n?.kind === 'task') return [{ kind: 'note', at: m.at, id: m.id, message: m, task: n.task }];
      if (n?.kind === 'delivery' && scope === 'mine') return [];
      return [{ kind: 'message', at: m.at, id: m.id, message: m }];
    }),
    ...items.map((i): TimelineEntry => ({ kind: 'item', at: i.at, id: i.id, item: i })),
  ];
  return out.sort((a, b) =>
    a.at < b.at ? -1 : a.at > b.at ? 1 : (a.kind === 'item') === (b.kind === 'item') ? a.id - b.id : a.kind === 'item' ? 1 : -1,
  );
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
/** A message of the person's that is not answered yet can be taken back; `confirm` when the agent already has it (it is told to stop). Null when it cannot. */
export function takeBack(m: ChatMessage): { confirm: boolean } | null {
  if (!['queued', 'typed', 'delivered', 'delivered_unanswered'].includes(m.landing)) return null;
  return { confirm: m.receipt ? !waiting(m) : m.landing === 'delivered' || m.landing === 'delivered_unanswered' };
}

/** Queued behind a running turn: the agent has not been given it. */
export const waiting = (m: ChatMessage) => (m.receipt ? m.receipt.state === 'queued' : m.landing === 'queued');
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
