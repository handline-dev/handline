// The pure side of chat: how messages and transcript items are ordered, who a message is from, and what can still be done to it.
// Fetching and keeping the conversation current is the apps' shared query layer (@handline/app-data).
import type { ChatMessage, Landing, Proposal, TranscriptItem } from '@handline/client';

export type TimelineEntry =
  | { kind: 'message'; at: string; id: number; message: ChatMessage }
  /** The task board's word about a task (`T<N> done by x: ...`), drawn as a task card, not a bubble. */
  | { kind: 'note'; at: string; id: number; message: ChatMessage; task: number }
  | { kind: 'item'; at: string; id: number; item: TranscriptItem }
  /** An HR proposal that was decided: it keeps its place at the time it was settled. */
  | { kind: 'proposal'; at: string; id: number; proposal: Proposal };

/** The proposals about this agent that were decided: they sit in the timeline where they were settled. */
export const decidedProposals = (all: readonly Proposal[] | undefined, agent: string): Proposal[] =>
  (all ?? []).filter((p) => (p.agent === agent || p.requested_by === agent) && (p.state === 'approved' || p.state === 'rejected'));

/** The sender of the daemon's own notices; its words are never the person's. */
export const NOTICE_SENDER = 'task-board';
/** What a task-board message is: a task's news (`T<N> done by ...`), or a delivery notice (`#<N> to x was written ...`) that only concerns the sender. */
export function noticeOf(m: ChatMessage): { kind: 'task'; task: number } | { kind: 'delivery' } | null {
  if (m.from !== NOTICE_SENDER) return null;
  const task = /^T(\d+)\s/.exec(m.body);
  if (task) return { kind: 'task', task: Number(task[1]) };
  return /^#\d+\s/.test(m.body) ? { kind: 'delivery' } : null;
}

/** A permission request is a tool step (what the agent asked to run), so it is Activity's like the tool line; other dialogs stay in Chat. */
const isToolStep = (i: TranscriptItem) => i.kind === 'tool' || (i.kind === 'prompt' && i.prompt?.kind === 'permission');

/** Everything on screen for one agent, oldest first: the messages and the agent's transcript by time. In the agent's own chat (`mine`) nothing the task board sent and no tool line is shown: they still reach the agent and stay on the Activity tab. */
export function timeline(
  messages: ChatMessage[],
  items: TranscriptItem[],
  scope: 'mine' | 'all' = 'mine',
  agent?: string,
  me = '',
  decided: readonly Proposal[] = [],
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
      .filter((i) => !(scope === 'mine' && (isToolStep(i) || (agent && i.task_event && i.task_event.who !== agent && i.task_event.who !== 'human'))))
      .map((i): TimelineEntry => ({ kind: 'item', at: i.at, id: i.id, item: i })),
    ...decided.flatMap((p): TimelineEntry[] => (p.settled ? [{ kind: 'proposal', at: p.settled, id: p.id, proposal: p }] : [])),
  ];
  const rank = (e: TimelineEntry) => (e.kind === 'item' ? 1 : e.kind === 'proposal' ? 2 : 0); // at one instant: messages, then the transcript, then a decision
  out.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : rank(a) - rank(b) || a.id - b.id));
  return out.reduce<TimelineEntry[]>((all, e) => {
    const prev = all.at(-1);
    if (prev?.kind === 'item' && e.kind === 'item' && prev.item.kind === 'session' && e.item.kind === 'session')
      all[all.length - 1] = mergeSessions(prev, e);
    else all.push(e);
    return all;
  }, []);
}

const PLAIN_START = new Set(['launch', 'start']);
/** One restart is one line: back-to-back session starts collapse into the last, which keeps the handoff and the cause the run was told (a plain launch is the least telling cause). */
function mergeSessions(a: Extract<TimelineEntry, { kind: 'item' }>, b: Extract<TimelineEntry, { kind: 'item' }>): TimelineEntry {
  const [x, y] = [a.item.session, b.item.session];
  const cause = [y?.cause, x?.cause].find((c) => c && !PLAIN_START.has(c)) ?? y?.cause ?? x?.cause;
  const session = { ...y!, handoff: y?.handoff || x?.handoff || null, ...(cause ? { cause } : {}) };
  return { ...b, item: { ...b.item, session } };
}

/** The agent's Chat: the person's words and the agent's, its replies and its other prompts, and its session marks. Task and tool lines, and permission requests, are Activity's. */
export const chatEntries = (messages: ChatMessage[], items: TranscriptItem[], agent: string, decided: Proposal[] = []): TimelineEntry[] =>
  timeline(messages, items, 'mine', agent, '', decided).filter((e) => !(e.kind === 'item' && e.item.kind === 'task_event'));

/** The agent's Activity, read-only: what the Chat leaves out (other people's messages, task notices) and every task, session and tool line. `all` is the daemon's all-scope messages, `mine` its mine-scope ones: what the Chat draws from the second is left out of the first. */
export function activity(all: ChatMessage[], mine: ChatMessage[], items: TranscriptItem[], agent: string): Exclude<TimelineEntry, { kind: 'proposal' }>[] {
  const inChat = new Set(timeline(mine, [], 'mine', agent).map((e) => e.id));
  return timeline(all, items, 'all', agent).filter(
    (e): e is Exclude<TimelineEntry, { kind: 'proposal' }> =>
      e.kind === 'proposal' ? false : e.kind === 'item' ? isToolStep(e.item) || ['session', 'task_event'].includes(e.item.kind) : !inChat.has(e.id),
  );
}

export type ActivityKind = 'messages' | 'tasks' | 'sessions' | 'tools';
export const ACTIVITY_KINDS: readonly ActivityKind[] = ['messages', 'tasks', 'sessions', 'tools'];
export interface ActivityRow {
  key: string;
  at: string;
  kind: ActivityKind;
  /** `from -> to` for a message, else null. */
  route: { from: string; to: string } | null;
  text: string;
  /** What a tool line printed, and how many lines the daemon let go. */
  output?: string;
  dropped?: number;
}

/** What the Activity tab lists for one agent, oldest first: what it does with the team, not what it says to the person. Messages and notices of the *all* scope that the person is not part of, task, session and tool lines of the transcript; the agent's own replies and prompts stay in Chat. */
export function teamActivityOf(messages: ChatMessage[], items: TranscriptItem[], me: string): ActivityRow[] {
  const rows: ActivityRow[] = [];
  for (const m of messages) {
    if (isPerson(m.from, me) || isPerson(m.to, me) || m.proposal != null) continue;
    const n = noticeOf(m);
    if (n?.kind === 'delivery') continue;
    rows.push({ key: `m${m.id}`, at: m.at, kind: n ? 'tasks' : 'messages', route: { from: m.from, to: m.to }, text: m.body });
  }
  for (const i of items) {
    if (i.kind === 'tool') rows.push({ key: `i${i.id}`, at: i.at, kind: 'tools', route: null, text: [i.tool, i.text].filter(Boolean).join(' '), output: i.output, dropped: i.dropped_lines });
    else if (i.kind === 'prompt' && i.prompt?.kind === 'permission')
      rows.push({ key: `i${i.id}`, at: i.at, kind: 'tools', route: null, text: `Asked permission: ${i.prompt.tool ?? ''}${i.prompt.command ? ` ${i.prompt.command}` : ''}${i.open ? ' (waiting)' : ''}` });
    else if (i.kind === 'session') rows.push({ key: `i${i.id}`, at: i.at, kind: 'sessions', route: null, text: sessionLine(i).reason });
    else if (i.kind === 'task_event' && i.task_event) {
      const e = i.task_event;
      rows.push({ key: `i${i.id}`, at: i.at, kind: 'tasks', route: null, text: `T${e.task} ${e.event}${e.change ? `: ${e.change}` : ''} by ${e.who}` });
    }
  }
  return rows.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : a.key < b.key ? -1 : 1));
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
  // delivered_unanswered left the contract, but a daemon still sends it until it drops the answer model: it is delivered.
  const delivered = m.landing === 'delivered' || (m.landing as string) === 'delivered_unanswered';
  if (!delivered && !['queued', 'typed'].includes(m.landing)) return null;
  return { confirm: m.receipt ? !waiting(m) : delivered };
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
  // Gone from the contract, but a daemon still sends these three until it drops the answer model; a message in them is delivered.
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

const RESTART_WHY: Record<string, string> = {
  handoff: 'from a handoff',
  restart: 'restarted',
  'fresh close': 'fresh conversation',
  wake: 'woke from a close',
  move: 'moved to another account',
  context_full: 'context was full',
  settings_applied: 'settings applied',
  crash_recovery: 'resumed after a crash',
  idle_close: 'woke after idle close',
  launch: 'launched',
  start: 'started',
};
/** The short cause after `New session at <time>`: the daemon's word in plain words, an unknown one as it came, null when the daemon sent none. */
export function restartCause(i: TranscriptItem): string | null {
  const cause = i.session?.cause?.trim();
  return cause ? (RESTART_WHY[cause] ?? cause) : null;
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

/** Whether the restart line still shows: false once its New session line is in the chat, so one restart never shows both. */
export function restartShown(h: { since: string }, items: TranscriptItem[]): boolean {
  const since = Date.parse(h.since);
  return !items.some((i) => i.kind === 'session' && Date.parse(i.at) >= since);
}

/** The live restart line: `Restarting: context full. Writing the handoff…`. */
export function restartingLine(h: { cause: string; phase: 'writing' | 'starting' }): string {
  const { why, what } = handoffLine(h);
  return `Restarting: ${why.charAt(0).toLowerCase()}${why.slice(1)}. ${what}…`;
}
