// The fake daemon's chat messages and transcripts: what chat.messages, chat.transcript and the finders answer, and the one place a message changes.
import type { ChatMessage, Landing, TranscriptItem, Working } from '@handline/contract';
import { searchHits } from '#src/chat-search.ts';

export type Emit = (
  type: 'chat.message' | 'chat.transcript' | 'chat.working',
  agent: string,
  data: ChatMessage | TranscriptItem | Working,
) => void;

export class ChatFailure extends Error {
  code: 'not_found' | 'refused' | 'bad_request';
  gate?: string;
  constructor(code: 'not_found' | 'refused' | 'bad_request', message: string, gate?: string) {
    super(message);
    this.code = code;
    this.gate = gate;
  }
}

export interface Thread {
  items: TranscriptItem[];
  working: Working;
}
export const IDLE: Working = { turn_running: false, line: null, since: null };

export class ChatStore {
  protected messages: (ChatMessage & { agent: string })[] = [];
  protected threads = new Map<string, Thread>();
  protected steps: (() => void)[] = [];
  protected nextId = 100;
  /** Item ids are one run across agents, so an id of another agent's transcript is no item of this one. */
  protected nextItem = 1;

  protected emit: Emit;
  protected now: () => string;
  protected sessions: Map<string, { structured?: boolean; state: string }>;

  constructor(emit: Emit, now: () => string, sessions: Map<string, { structured?: boolean; state: string }>) {
    this.emit = emit;
    this.now = now;
    this.sessions = sessions;
  }

  protected thread(agent: string): Thread {
    let t = this.threads.get(agent);
    if (!t) this.threads.set(agent, (t = { items: [], working: { ...IDLE } }));
    return t;
  }
  /** A message by its number, its msg_ name or a bare hash (6 to 12 hex characters of its ref), as requestref.Resolve reads them. */
  find(id: unknown): ChatMessage & { agent: string } {
    let found = this.messages.filter((x) => x.id === id);
    if (typeof id === 'string') {
      const hash = id.trim().toLowerCase().replace(/^msg_/, '');
      if (hash.startsWith('#')) throw new ChatFailure('bad_request', `${hash} is a pull request; a message is msg_<hash>`);
      if (!/^[0-9a-f]{6,12}$/.test(hash)) throw new ChatFailure('bad_request', 'a message is msg_<hash>, or 6 to 12 hex characters of it');
      found = this.messages.filter((x) => x.ref?.startsWith(hash));
      if (found.length > 1) throw new ChatFailure('bad_request', `msg_${hash} is ambiguous: give more of the hash`);
    }
    if (!found.length) throw new ChatFailure('not_found', `no message ${String(id)}`);
    return found[0];
  }
  /** The message whose run in that window has not ended. */
  running(terminal: string): (ChatMessage & { agent: string }) | undefined {
    return this.messages.find((m) => m.runs?.some((r) => r.terminal === terminal && r.state === 'running'));
  }
  change(m: ChatMessage & { agent: string }, patch: Partial<ChatMessage>) {
    Object.assign(m, patch, { state_at: this.now() });
    this.emit('chat.message', m.agent, this.view(m));
  }
  protected view({ agent: _agent, ...m }: ChatMessage & { agent: string }): ChatMessage {
    return { ...m, tasks: [...m.tasks] };
  }

  /** Adds a message without events: history that was there before any client connected. */
  seed(agent: string, m: Partial<ChatMessage> & { body: string; from: string; to: string }): number {
    const at = m.at ?? this.now();
    const id = this.nextId++;
    const msg = {
      id,
      ref: (Math.imul(id, 0x9e3779b1) >>> 0).toString(16).padStart(8, '0') + id.toString(16).padStart(4, '0'), // a hash's look: prefixes differ
      at,
      state_at: at,
      state: m.landing ?? 'delivered',
      landing: 'delivered' as Landing,
      sending_reason: null,
      delivered_at: at,
      typed_at: at,
      landed_at: at,
      parent: null,
      kind: 'message',
      tasks: [],
      ...m,
      agent,
    } as ChatMessage & { agent: string };
    msg.state = msg.landing;
    this.messages.push(msg);
    return msg.id;
  }
  seedItem(agent: string, item: Omit<TranscriptItem, 'id' | 'at'> & { at?: string }) {
    const t = this.thread(agent);
    t.items.push({ id: this.nextItem++, at: item.at ?? this.now(), ...item });
  }
  /** A question from the agent to the person, waiting for the person. */
  ask(agent: string, body: string, person: string): number {
    const at = this.now();
    const id = this.seed(agent, {
      body,
      from: agent,
      to: person,
      at,
      kind: 'question',
    });
    this.emit('chat.message', agent, this.view(this.find(id)));
    return id;
  }

  // ---- methods ----

  messagesOf(p: Record<string, unknown>) {
    const agent = String(p.agent);
    const mine = p.scope === 'mine';
    const limit = typeof p.limit === 'number' ? p.limit : 50;
    let list = this.messages.filter(
      (m) => m.agent === agent && !(m.receipt && m.landing === 'withdrawn') && (!mine || this.shownInMine(m)),
    );
    if (typeof p.before === 'number') list = list.filter((m) => m.id < (p.before as number));
    if (typeof p.since === 'number') list = list.filter((m) => m.id > (p.since as number));
    // since pages forward from the id (the oldest after it first), before pages back from the newest.
    list = list.sort((a, b) => (typeof p.since === 'number' ? a.id - b.id : b.id - a.id));
    const page = list.slice(0, limit).sort((a, b) => b.id - a.id);
    return { messages: page.map((m) => this.view(m)), more: list.length > limit };
  }

  transcriptOf(p: Record<string, unknown>) {
    const t = this.thread(String(p.agent));
    const limit = typeof p.limit === 'number' ? p.limit : 50;
    let items = t.items;
    if (typeof p.before === 'number') items = items.filter((i) => i.id < (p.before as number));
    if (typeof p.since === 'number') items = items.filter((i) => i.id > (p.since as number));
    return { items: items.slice(-limit), more: items.length > limit, working: t.working };
  }

  /** The newest message number the person has seen per agent. */
  readonly seenUpTo = new Map<string, number>();
  seen(p: Record<string, unknown>) {
    if (typeof p.agent !== 'string' || typeof p.up_to !== 'number') throw new ChatFailure('bad_request', 'agent and up_to are needed');
    this.seenUpTo.set(p.agent, Math.max(this.seenUpTo.get(p.agent) ?? 0, p.up_to));
  }
  /** The page of messages around a message, newest first, in the chat that shows it, and the agent's transcript items of the same span. */
  locate(p: Record<string, unknown>) {
    const m = this.find(p.request);
    const agent = typeof p.agent === 'string' ? p.agent : m.agent;
    const scope = this.shownInMine(m) ? 'mine' : 'all';
    const list = this.messages.filter((x) => x.agent === agent).sort((a, b) => b.id - a.id);
    const at = list.findIndex((x) => x.id === m.id);
    const limit = typeof p.limit === 'number' ? p.limit : 50;
    const from = Math.max(0, at - Math.floor(limit / 2));
    const page = list.slice(from, from + limit);
    const second = (at: string) => Math.floor(Date.parse(at) / 1000);
    const start = second(page[page.length - 1].at);
    const end = from > 0 ? second(page[0].at) + 1 : Infinity;
    const span = this.thread(agent).items.filter((i) => second(i.at) >= start && second(i.at) < end);
    const centre = span.findIndex((i) => second(i.at) >= second(m.at));
    const lo = Math.max(0, Math.min((centre < 0 ? span.length : centre) - 100, span.length - 200));
    return {
      agent,
      scope,
      message: this.view(m),
      messages: page.map((x) => this.view(x)),
      more_older: from + limit < list.length,
      more_newer: from > 0,
      transcript: span.slice(lo, lo + 200),
      transcript_more_older: lo > 0,
      transcript_more_newer: lo + 200 < span.length,
    };
  }
  /** What a # can point at: the agent's newest requests, sent or received, as a line each (the first line, up to 60 characters). */
  refs(p: Record<string, unknown>) {
    const agent = String(p.agent);
    const requests = this.messages
      .filter((m) => m.agent === agent && m.landing !== 'withdrawn')
      .sort((a, b) => b.id - a.id)
      .slice(0, 30)
      .map((m) => ({ request: m.id, from: m.from, to: m.to, line: [...(m.body.trim().split('\n')[0] ?? '')].slice(0, 60).join('') }));
    return { requests };
  }
  /** Links a message to a task, as the daemon does for a question asked from the task's page. */
  link(id: number, task: { id: number; title: string }) {
    const m = this.find(id);
    if (!m.tasks.some((t) => t.id === task.id)) this.change(m, { tasks: [...m.tasks, task] });
  }
  /** Messages whose body holds the text, newest first, each with the agent and the chat (scope) that shows it. */
  search(p: Record<string, unknown>) {
    const hits = searchHits(this.messages, p, (m) => this.shownInMine(m));
    return { messages: hits.map((m) => ({ ...this.view(m), agent: m.agent, scope: this.shownInMine(m) ? 'mine' : 'all' })) };
  }
  protected shownInMine(m: ChatMessage & { agent: string }): boolean {
    return !this.sessions.has(m.from) || !this.sessions.has(m.to);
  }
}
