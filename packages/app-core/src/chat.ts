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

/** Everything on screen for one agent, oldest first: the messages and the agent's transcript by time. In the agent's own chat (`mine`) nothing the task board sent is shown: it still reaches the agent and stays on the task. */
export function timeline(
  messages: ChatMessage[],
  items: TranscriptItem[],
  scope: 'mine' | 'all' = 'mine',
  agent?: string,
  me = '',
): TimelineEntry[] {
  const out: TimelineEntry[] = [
    ...messages.flatMap((m): TimelineEntry[] => {
      if (scope === 'mine' && m.from === NOTICE_SENDER) return [];
      if (scope === 'mine' && m.proposal != null) return []; // the outcome of an HR proposal is drawn as its card, not as this line
      // What this agent says to another agent is that agent's room's, not this one's: the all tab shows what reaches the agent and what it says to the person.
      if (scope === 'all' && agent && m.from === agent && m.to !== agent && !isPerson(m.to, me)) return [];
      const n = noticeOf(m);
      if (n?.kind === 'task') return [{ kind: 'note', at: m.at, id: m.id, message: m, task: n.task }];
      return [{ kind: 'message', at: m.at, id: m.id, message: m }];
    }),
    // A task card sits in the chat of the agent that made the change, or of the founder acting for it (`human`); the *all* tab shows every one.
    ...items
      .filter((i) => !(scope === 'mine' && agent && i.task_event && i.task_event.who !== agent && i.task_event.who !== 'human'))
      .map((i): TimelineEntry => ({ kind: 'item', at: i.at, id: i.id, item: i })),
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
/** A message of the person's can be taken back while it is not written or delivered; `confirm` when the agent already has it (it is told to stop). Null when it cannot. */
export function takeBack(m: ChatMessage): { confirm: boolean } | null {
  if (!['queued', 'typed', 'delivered', 'delivered_unanswered'].includes(m.landing)) return null;
  return { confirm: m.receipt ? !waiting(m) : m.landing === 'delivered' || m.landing === 'delivered_unanswered' };
}

/** Queued behind a running turn: the agent has not been given it. */
export const waiting = (m: ChatMessage) => (m.receipt ? m.receipt.state === 'queued' : m.landing === 'queued');

/** How far the person's own message got, for the two ticks: 0 not sent yet, 1 sent and not read, 2 read. */
export function tickLevel(m: ChatMessage): 0 | 1 | 2 {
  if (m.id < 0 || waiting(m)) return 0;
  if (m.receipt) return m.receipt.state === 'confirmed' ? 2 : 1;
  return m.landing === 'typed' ? 1 : 2;
}

const LABELS: Record<string, string> = {
  queued: 'Sending',
  typed: 'Typed',
  delivered: 'Delivered',
  // The daemon still sends these three until the answer model leaves the contract; a message in them is delivered.
  delivered_unanswered: 'Delivered',
  answered: 'Delivered',
  dismissed: 'Delivered',
  withdrawn: 'Withdrawn',
};
export const landingLabel = (l: Landing): string => LABELS[l] ?? l;

const SESSION_WHY: Record<string, string> = {
  handoff: 'Started again from a handoff',
  restart: 'Restarted',
  'fresh close': 'Restarted with a fresh conversation',
  wake: 'Woke from a close',
  move: 'Moved to another account',
  context_full: 'Context full, started again from a handoff',
  settings_applied: 'Restarted to apply settings',
  crash_recovery: 'Resumed after a crash',
  idle_close: 'Woke after closing when idle',
  launch: 'Launched',
  start: 'Started',
};
/** What a session line says: the reason a new session began in plain words (the cause is an open set, so an unknown one is shown as it came), and the handoff text it began with. */
export function sessionLine(i: TranscriptItem): { reason: string; handoff: string | null } {
  const cause = i.session?.cause?.trim();
  const reason = !cause ? 'New session' : (SESSION_WHY[cause] ?? `New session (${cause})`);
  return { reason, handoff: i.session?.handoff || null };
}

const TASK_LABEL: Record<string, string> = { opened: 'Task added', changed: 'Task changed', done: 'Task done', withdrawn: 'Task deleted' };
/** What a task line in the chat shows: the label (added / edited / done / deleted), the task, and only what changed, briefly. */
export interface TaskRow {
  label: string;
  task: number;
  title: string | null;
  /** Who made the change (the agent, or `human`). */
  who: string | null;
  /** The change (`priority 2 -> 1`) or the evidence (done note, deletion reason); null when there is none. */
  detail: string | null;
  /** The detail as pieces: a change written `<field> <old> -> <new>` is one piece with both values, anything else is plain text. */
  parts: TaskChange[];
}
export type TaskChange = { field: string; old: string; now: string } | { text: string };

/** Splits `assigned to X, priority 2 -> 1` at its commas; a piece of the form `[field ]old -> new` keeps its two values. */
export function changeParts(detail: string | null): TaskChange[] {
  if (!detail) return [];
  return detail.split(', ').map((piece): TaskChange => {
    const m = /^(?:(.+) )?(\S+) -> (.+)$/.exec(piece);
    return m ? { field: m[1] ?? '', old: m[2]!, now: m[3]! } : { text: piece };
  });
}
export function taskRow(i: TranscriptItem, titles?: Map<number, string>): TaskRow {
  const e = i.task_event;
  const ev = e?.event ?? 'changed';
  const change = (e as { change?: string | null } | undefined)?.change;
  return {
    label: TASK_LABEL[ev] ?? 'Task changed',
    task: e?.task ?? NaN,
    title: titles?.get(e?.task ?? NaN) ?? null,
    who: e?.who ?? null,
    detail: change || e?.evidence || null,
    parts: changeParts(change || e?.evidence || null),
  };
}

const HANDOFF_WHY: Record<string, string> = {
  context_full: 'Context full',
  move: 'Moving to another account',
  usage: 'Usage limit',
  login: 'Login needed',
};
/** The live line while a session hands off: why it ends (the cause is an open set, so an unknown one is shown as it came) and what is happening now. */
export function handoffLine(h: { cause: string; phase: 'writing' | 'starting' }): { why: string; what: string } {
  return {
    why: HANDOFF_WHY[h.cause] ?? h.cause,
    what: h.phase === 'writing' ? 'Writing the handoff' : 'Starting a new session',
  };
}
