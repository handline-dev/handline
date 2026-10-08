// The fake daemon's transcript stream: the events of the agents it plays as structured sessions, on the transcript topic.
import type { Actor, Prompt, Timestamp, TranscriptEvent, TranscriptEvents, Working } from '@handline/contract';
import { badRequest, Failure, refused } from './errors.ts';
import { seedTranscript } from './fixtures.ts';
import type { Session } from './session.ts';

interface Host {
  sessions: Map<string, Session>;
  unservedTopics: Set<string>;
  emit(topic: 'transcript', type: string, f: { agent: string; data: Record<string, unknown> }): void;
}

type Body = Pick<TranscriptEvent, 'kind'> & Partial<Omit<TranscriptEvent, 'agent' | 'session' | 'seq' | 'at' | 'kind'>>;
const IDLE: Working = { turn_running: false, line: null, since: null };
const int = (v: unknown, field: string, lo: number, hi = Infinity) => {
  if (!Number.isInteger(v) || (v as number) < lo || (v as number) > hi) throw badRequest(`${field} is an integer from ${lo}`, field);
  return v as number;
};

export class FakeTranscript {
  private logs = new Map<string, TranscriptEvent[]>();
  private last = new Map<string, number>(); // the newest seq of each agent, kept when events are forgotten
  private host: Host;
  private now: () => Timestamp;
  readonly on: boolean;

  /** `on` false is a daemon that serves no transcript: it lists no method and refuses the topic. */
  constructor(host: Host, now: () => Timestamp, on: boolean) {
    this.host = host;
    this.now = now;
    this.on = on;
    if (on) seedTranscript(this);
    else host.unservedTopics.add('transcript');
  }

  /** Whether the daemon lists this method. */
  lists = (name: string): boolean => this.on || name !== 'transcript.events';

  /** Plays `agent` as a structured session from now on. */
  structure(agent: string): void {
    if (!this.logs.has(agent)) this.logs.set(agent, []);
    this.host.sessions.get(agent)!.structured = true;
  }
  owns = (agent: string): boolean => this.logs.has(agent);

  /** Retention: drops the events of `agent` up to and including `seq`, as a daemon that keeps no more would; a read from before that says gap. */
  forget(agent: string, seq: number): void {
    this.logs.set(agent, this.events(agent).filter((e) => e.seq > seq));
  }

  /** The topic needs member, as the read does: an agent session that subscribes to it is refused. */
  gate<T extends string>(topics: T[], role: string): T[] {
    if (role === 'agent' && topics.includes('transcript' as T)) throw refused('role', 'the transcript is not for an agent session');
    return topics;
  }

  events = (agent: string): readonly TranscriptEvent[] => this.logs.get(agent) ?? [];

  /** Adds the next event of `agent`: seq, session and time are the daemon's. A test or demo drives the stream with this. */
  push(agent: string, body: Body, session = 's-1'): TranscriptEvent {
    const log = this.logs.get(agent);
    if (!log) throw new Error(`${agent} is not a structured session`);
    const e = { turn: null, item: null, ...body, agent, session, seq: (this.last.get(agent) ?? 0) + 1, at: this.now() } as TranscriptEvent;
    log.push(e);
    this.last.set(agent, e.seq);
    this.host.emit('transcript', 'transcript.event', { agent, data: e as unknown as Record<string, unknown> });
    return e;
  }

  /** The model calls a tool and a person is asked: tool_use, then a permission card that prompt.answer answers. Returns the tool call's id. */
  ask(agent: string, turn: string, tool: string, command: string): string {
    const id = `toolu_${(this.last.get(agent) ?? 0) + 1}`;
    this.push(agent, { kind: 'tool_use', turn, tool_use: { id, tool, input: { command } } });
    const prompt: Prompt = {
      id,
      agent,
      kind: 'permission',
      tool,
      command,
      reason: null,
      lines: [],
      since: this.now(),
      options: [
        { key: 'allow', label: 'Allow', answer: 'yes', highlighted: true },
        { key: 'deny', label: 'Deny', answer: 'no' },
      ],
    };
    this.host.sessions.get(agent)!.prompt = prompt;
    this.push(agent, { kind: 'permission', turn, permission: { tool_use_id: id, prompt } });
    return id;
  }

  /** prompt.answer on an open permission: the answer, then the tool's result (or its refusal) and the end of the turn. */
  answered(agent: string, promptId: string, key: string, actor: Actor): void {
    const asked = this.events(agent).find((e) => e.kind === 'permission' && e.permission?.tool_use_id === promptId);
    if (!asked) return;
    const allow = key === 'allow',
      turn = asked.turn;
    this.host.sessions.get(agent)!.prompt = null;
    this.push(agent, {
      kind: 'permission_answer',
      turn,
      permission_answer: {
        tool_use_id: promptId,
        behavior: allow ? 'allow' : 'deny',
        by: 'person',
        rule: null,
        message: allow ? null : `${actor.name} denied it`,
      },
    });
    this.push(agent, {
      kind: 'tool_result',
      turn,
      tool_result: { tool_use_id: promptId, ok: allow, text: allow ? 'done' : 'denied', truncated: false },
    });
    this.push(agent, {
      kind: 'turn_end',
      turn,
      turn_end: { reason: 'done', cost_usd: 0.01, duration_ms: 3000, usage: { input: 800, output: 90, cache_read: 4000, cache_write: 0 } },
    });
  }

  /** transcript.events. */
  read(p: Record<string, unknown>): TranscriptEvents {
    const agent = p.agent;
    if (typeof agent !== 'string') throw badRequest('agent is a string', 'agent');
    if (p.since_seq !== undefined && p.before_seq !== undefined)
      throw badRequest('since_seq and before_seq are not given together', 'since_seq');
    const since = p.since_seq === undefined ? undefined : int(p.since_seq, 'since_seq', 0);
    const before = p.before_seq === undefined ? undefined : int(p.before_seq, 'before_seq', 1);
    const limit = p.limit === undefined ? 200 : int(p.limit, 'limit', 1, 500);
    if (!this.host.sessions.has(agent)) throw new Failure('not_found', `no agent named ${agent}`);
    const log = this.logs.get(agent);
    if (!log) throw refused('unsupported', `${agent} runs as a terminal and has no transcript stream`);
    const first = log[0]?.seq ?? 0,
      gap = since !== undefined && first > 0 && since < first - 1;
    const pool = since !== undefined ? log.filter((e) => e.seq > since) : log.filter((e) => before === undefined || e.seq < before);
    const events = since !== undefined ? pool.slice(0, limit) : pool.slice(-limit);
    const open = log.some(
      (e) =>
        e.kind === 'permission' &&
        !log.some((a) => a.kind === 'permission_answer' && a.permission_answer?.tool_use_id === e.permission?.tool_use_id),
    );
    return {
      events: events.map((e) => ({ ...e })),
      more: pool.length > limit,
      first_seq: first,
      last_seq: this.last.get(agent) ?? 0,
      gap,
      working: open ? { turn_running: true, line: 'waiting for a permission answer', since: null } : IDLE,
    };
  }
}
