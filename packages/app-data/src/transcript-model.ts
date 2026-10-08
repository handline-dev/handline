// A structured session's transcript events as a view draws them: kept in seq order with the breaks the daemon's retention left,
// and folded into turns whose tool calls carry their result and their permission card.
import type { Prompt, TranscriptEvent } from '@handline/client';

export type Answer = NonNullable<TranscriptEvent['permission_answer']>;
export type TurnEnd = NonNullable<TranscriptEvent['turn_end']>;

export type Entry =
  | { type: 'text'; seq: number; text: string }
  | {
      type: 'tool';
      seq: number;
      id: string;
      /** Null when the tool_use lies before the oldest event held. */
      tool: string | null;
      input: { [key: string]: unknown };
      result: { ok: boolean; text: string; truncated: boolean } | null;
      /** The card a person answers with prompt.answer (choice allow or deny); `open` until a permission_answer ends it. */
      permission: { prompt: Prompt; open: boolean } | null;
      answer: Answer | null;
    }
  | { type: 'error'; seq: number; code: string; text: string }
  /** Events between the one before and `seq` are gone from the daemon. */
  | { type: 'gap'; seq: number };

export interface Turn {
  /** The turn id; null for what happened outside a turn. */
  id: string | null;
  /** The seq of its first event. */
  seq: number;
  message: { uuid: string; from: string | null; text: string; at: string } | null;
  entries: Entry[];
  end: TurnEnd | null;
  /** The last turn, begun and not ended. */
  running: boolean;
}

export interface Log {
  /** Ascending by seq, no duplicates. */
  events: TranscriptEvent[];
  /** Seqs that follow a stretch the daemon no longer keeps. */
  breaks: number[];
}
export const EMPTY: Log = { events: [], breaks: [] };

export const lastSeq = (log: Log) => log.events.at(-1)?.seq ?? 0;

/** Adds events, dropping any seq already held. `breakAt` marks that nothing lies between the event before it and that seq. */
export function merge(log: Log, incoming: readonly TranscriptEvent[], breakAt?: number): Log {
  const held = new Set(log.events.map((e) => e.seq));
  const fresh = incoming.filter((e, i) => !held.has(e.seq) && incoming.findIndex((x) => x.seq === e.seq) === i);
  const events = fresh.length ? [...log.events, ...fresh].sort((a, b) => a.seq - b.seq) : log.events;
  const breaks = breakAt !== undefined && !log.breaks.includes(breakAt) ? [...log.breaks, breakAt].sort((a, b) => a - b) : log.breaks;
  return events === log.events && breaks === log.breaks ? log : { events, breaks };
}

/** Two logs as one: every event of both, and the breaks of both. */
export function join(a: Log, b: Log): Log {
  const m = merge(a, b.events);
  const breaks = [...new Set([...m.breaks, ...b.breaks])].sort((x, y) => x - y);
  return breaks.length === m.breaks.length ? m : { events: m.events, breaks };
}

/** The first seq after which an event is missing from what is held, if any (the hole a live event with seq > last + 1 leaves). */
export function firstHole(log: Log): number | null {
  for (let i = 1; i < log.events.length; i++) {
    const seq = log.events[i]!.seq;
    if (seq !== log.events[i - 1]!.seq + 1 && !log.breaks.includes(seq)) return log.events[i - 1]!.seq;
  }
  return null;
}

const KNOWN = new Set(['turn_start', 'text', 'tool_use', 'tool_result', 'permission', 'permission_answer', 'turn_end', 'error']);

/** Folds the log into turns. A kind it does not know is skipped; a tool_result or answer whose tool_use is not held makes a tool entry of its own. */
export function toTurns(log: Log): Turn[] {
  const turns: Turn[] = [];
  const tools = new Map<string, Extract<Entry, { type: 'tool' }>>();
  const toolFor = (turn: Turn, seq: number, id: string) => {
    let t = tools.get(id);
    if (!t) {
      t = { type: 'tool', seq, id, tool: null, input: {}, result: null, permission: null, answer: null };
      tools.set(id, t);
      turn.entries.push(t);
    }
    return t;
  };
  let cur: Turn | null = null;
  for (const e of log.events) {
    if (!KNOWN.has(e.kind)) continue;
    if (!cur || e.turn !== cur.id || e.kind === 'turn_start') {
      cur = { id: e.turn, seq: e.seq, message: null, entries: [], end: null, running: false };
      turns.push(cur);
    }
    if (log.breaks.includes(e.seq)) cur.entries.push({ type: 'gap', seq: e.seq });
    const turn = cur;
    if (e.kind === 'turn_start' && e.turn_start) {
      turn.message = { uuid: e.turn_start.message, from: e.turn_start.from, text: e.turn_start.text, at: e.at };
    } else if (e.kind === 'text') {
      turn.entries.push({ type: 'text', seq: e.seq, text: e.text ?? '' });
    } else if (e.kind === 'tool_use' && e.tool_use) {
      const t = toolFor(turn, e.seq, e.tool_use.id);
      t.tool = e.tool_use.tool;
      t.input = e.tool_use.input;
    } else if (e.kind === 'tool_result' && e.tool_result) {
      const { ok, text, truncated } = e.tool_result;
      toolFor(turn, e.seq, e.tool_result.tool_use_id).result = { ok, text, truncated };
    } else if (e.kind === 'permission' && e.permission) {
      toolFor(turn, e.seq, e.permission.tool_use_id).permission = { prompt: e.permission.prompt, open: true };
    } else if (e.kind === 'permission_answer' && e.permission_answer) {
      const t = toolFor(turn, e.seq, e.permission_answer.tool_use_id);
      t.answer = e.permission_answer;
      if (t.permission) t.permission = { ...t.permission, open: false };
    } else if (e.kind === 'turn_end' && e.turn_end) {
      turn.end = e.turn_end;
    } else if (e.kind === 'error' && e.error) {
      turn.entries.push({ type: 'error', seq: e.seq, code: e.error.code, text: e.error.text });
    }
  }
  const last = turns.at(-1);
  if (last && last.id !== null && last.message && !last.end) last.running = true;
  return turns;
}
