// The fake daemon's chat: messages with landing state, the transcript and the working line, on the chat topic.
// A sent message lands one tick at a time (typed, delivered, working, answered) so a UI sees every state; `tick` is driven by the daemon's own.
import { refused, storeFailure } from './errors.ts';
import type { ChatMessage, Landing, TranscriptItem, Working } from '@handline/contract';
import { deliveryOf, receiptPatch, uuidFor, type Msg } from './delivery.ts';

type Emit = (
  type: 'chat.message' | 'chat.transcript' | 'chat.working',
  agent: string,
  data: ChatMessage | TranscriptItem | Working,
) => void;
/** Runs a chat call and gives its failure as the JSON-RPC error the daemon sends. */
export function chatCall<T>(fn: () => T): T {
  try {
    return fn();
  } catch (e) {
    if (!(e instanceof ChatFailure)) throw e;
    throw storeFailure(e);
  }
}

/** One chat method. `checkAgent` throws for an agent that does not exist; `inboxDismiss` takes an inbox item off its list when the id is one. */
export function chatMethod(
  chat: FakeChat,
  method: string,
  p: Record<string, unknown>,
  person: string,
  checkAgent: () => unknown,
  ok: (said: string, extra?: object) => object,
  inboxDismiss: (id: unknown) => boolean,
): unknown {
  switch (method) {
    case 'chat.messages':
      checkAgent();
      return chat.messagesOf(p);
    case 'chat.transcript':
      checkAgent();
      return chat.transcriptOf(p);
    case 'chat.send':
      checkAgent();
      return chatCall(() => ok('Sent.', chat.send(p, person)));
    case 'chat.answer':
      return chatCall(() => (chat.answer(p), ok('Answered.')));
    case 'chat.dismiss':
      if (inboxDismiss(p.request)) return ok('Dismissed.');
      return chatCall(() => (chat.dismiss(p), ok('Dismissed.')));
    case 'chat.send_now':
      return chatCall(() => ok('Typed.', chat.sendNow(p)));
    case 'chat.cancel':
      return chatCall(() => (chat.cancel(p), ok('Taken back.')));
    case 'chat.seen':
      return chatCall(() => (chat.seen(p), ok('Seen.')));
    case 'chat.locate':
      return chatCall(() => chat.locate(p));
    default:
      throw refused('unsupported', `the fake daemon does not serve ${method}`);
  }
}

export class ChatFailure extends Error {
  code: 'not_found' | 'refused' | 'bad_request';
  gate?: string;
  constructor(code: 'not_found' | 'refused' | 'bad_request', message: string, gate?: string) {
    super(message);
    this.code = code;
    this.gate = gate;
  }
}

interface Thread {
  items: TranscriptItem[];
  working: Working;
  nextItem: number;
}
const IDLE: Working = { turn_running: false, line: null, since: null };

export class FakeChat {
  private messages: (ChatMessage & { agent: string })[] = [];
  private threads = new Map<string, Thread>();
  private steps: (() => void)[] = [];
  private nextId = 100;

  private emit: Emit;
  private now: () => string;
  private sessions: Map<string, { structured?: boolean; state: string }>;

  constructor(emit: Emit, now: () => string, sessions: Map<string, { structured?: boolean; state: string }>) {
    this.emit = emit;
    this.now = now;
    this.sessions = sessions;
  }

  private thread(agent: string): Thread {
    let t = this.threads.get(agent);
    if (!t) this.threads.set(agent, (t = { items: [], working: { ...IDLE }, nextItem: 1 }));
    return t;
  }
  find(id: unknown): ChatMessage & { agent: string } {
    const m = this.messages.find((x) => x.id === id);
    if (!m) throw new ChatFailure('not_found', `no message ${String(id)}`);
    return m;
  }
  change(m: ChatMessage & { agent: string }, patch: Partial<ChatMessage>) {
    Object.assign(m, patch, { state_at: this.now() });
    this.emit('chat.message', m.agent, this.view(m));
  }
  private view({ agent: _agent, ...m }: ChatMessage & { agent: string }): ChatMessage {
    return { ...m, tasks: [...m.tasks] };
  }

  /** Adds a message without events: history that was there before any client connected. */
  seed(agent: string, m: Partial<ChatMessage> & { body: string; from: string; to: string }): number {
    const at = m.at ?? this.now();
    const msg = {
      id: this.nextId++,
      at,
      state_at: at,
      state: m.landing ?? 'delivered',
      landing: 'delivered' as Landing,
      sending_reason: null,
      answer: null,
      dismissed: false,
      delivered_at: at,
      typed_at: at,
      landed_at: at,
      parent: null,
      kind: 'message',
      tasks: [],
      is_task_question: false,
      ...m,
      agent,
    } as ChatMessage & { agent: string };
    msg.state = msg.landing;
    this.messages.push(msg);
    return msg.id;
  }
  seedItem(agent: string, item: Omit<TranscriptItem, 'id' | 'at'> & { at?: string }) {
    const t = this.thread(agent);
    t.items.push({ id: t.nextItem++, at: item.at ?? this.now(), ...item });
  }
  /** A question from the agent to the person, waiting for chat.answer. */
  ask(agent: string, body: string, person: string): number {
    const at = this.now();
    const id = this.seed(agent, {
      body,
      from: agent,
      to: person,
      at,
      kind: 'question',
      is_task_question: true,
      landing: 'delivered_unanswered',
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
      (m) =>
        m.agent === agent && !(m.receipt && m.landing === 'withdrawn') && (!mine || !this.sessions.has(m.from) || !this.sessions.has(m.to)),
    );
    if (typeof p.before === 'number') list = list.filter((m) => m.id < (p.before as number));
    if (typeof p.since === 'number') list = list.filter((m) => m.id > (p.since as number));
    list = list.sort((a, b) => b.id - a.id);
    return { messages: list.slice(0, limit).map((m) => this.view(m)), more: list.length > limit };
  }

  transcriptOf(p: Record<string, unknown>) {
    const t = this.thread(String(p.agent));
    const limit = typeof p.limit === 'number' ? p.limit : 50;
    let items = t.items;
    if (typeof p.before === 'number') items = items.filter((i) => i.id < (p.before as number));
    if (typeof p.since === 'number') items = items.filter((i) => i.id > (p.since as number));
    return { items: items.slice(-limit), more: items.length > limit, working: t.working };
  }

  send(p: Record<string, unknown>, person: string) {
    const agent = String(p.agent),
      body = p.body;
    if (typeof body !== 'string' || !body.trim()) throw new ChatFailure('bad_request', 'a message needs a body');
    const open = this.sessions.get(agent)?.state !== 'parked';
    const delivery = deliveryOf(p);
    const s = this.sessions.get(agent);
    const receipt = s?.structured
      ? {
          state: s.state === 'running' && delivery === 'queue' ? ('queued' as const) : ('sent' as const),
          uuid: uuidFor(String(p.request_id)),
        }
      : undefined;
    const at = this.now();
    const id = this.seed(agent, {
      body,
      from: person,
      to: agent,
      at,
      parent: typeof p.parent === 'number' ? p.parent : null,
      landing: 'queued',
      delivered_at: null,
      typed_at: null,
      landed_at: null,
      sending_reason: open ? null : 'the agent has no open session',
    });
    if (receipt) Object.assign(this.find(id), receiptPatch({ ...this.find(id), receipt }, receipt.state, at));
    this.emit('chat.message', agent, this.view(this.find(id)));
    if (receipt?.state === 'sent') this.steps.push(() => this.settle(id, 'confirmed'));
    if (open && !receipt)
      this.steps.push(
        () => this.type(id),
        () => this.land(id),
        () => this.think(id),
        () => this.reply(id),
      );
    return { request: id, ...(receipt && { receipt: this.find(id).receipt }) };
  }
  private live(id: number) {
    const m = this.messages.find((x) => x.id === id);
    return m && m.landing !== 'withdrawn' ? m : undefined;
  }
  private type(id: number) {
    const m = this.live(id);
    if (m) this.change(m, { landing: 'typed', state: 'typed', typed_at: this.now() });
  }
  private land(id: number) {
    const m = this.live(id);
    if (m) this.change(m, { landing: 'delivered', state: 'delivered', landed_at: this.now(), delivered_at: this.now() });
  }
  /** The agent starts its turn: the working line shows and a tool runs. */
  private think(id: number) {
    const m = this.live(id);
    if (!m) return;
    const t = this.thread(m.agent);
    t.working = { turn_running: true, line: 'Reading the code', since: this.now() };
    this.emit('chat.working', m.agent, t.working);
    this.item(m.agent, { kind: 'tool', tool: 'Read', text: 'src/main.ts' });
  }
  /** The turn ends with a reply, and the message counts as answered. */
  private reply(id: number) {
    const m = this.live(id);
    if (!m) return;
    this.item(m.agent, { kind: 'reply', text: `Got it: "${m.body}"` });
    const t = this.thread(m.agent);
    t.working = { ...IDLE };
    this.emit('chat.working', m.agent, t.working);
    this.change(m, { landing: 'answered', state: 'answered' });
  }
  private item(agent: string, item: Omit<TranscriptItem, 'id' | 'at'>) {
    const t = this.thread(agent);
    const full: TranscriptItem = { id: t.nextItem++, at: this.now(), ...item };
    t.items.push(full);
    this.emit('chat.transcript', agent, full);
  }

  answer(p: Record<string, unknown>) {
    const m = this.find(p.request);
    if (m.answer || m.dismissed) throw new ChatFailure('refused', 'that message is already closed', 'state');
    if (typeof p.text !== 'string' || !p.text.trim()) throw new ChatFailure('bad_request', 'an answer needs text');
    this.change(m, { landing: 'answered', state: 'answered', answer: { id: this.nextId++, text: p.text, at: this.now() } });
  }
  dismiss(p: Record<string, unknown>) {
    const m = this.find(p.request);
    if (m.answer || m.dismissed) throw new ChatFailure('refused', 'that message is already closed', 'state');
    this.change(m, { landing: 'dismissed', state: 'dismissed', dismissed: true });
  }
  /** Types a queued message now, ahead of the scripted order. */
  sendNow(p: Record<string, unknown>) {
    const m = this.find(p.request);
    if (m.landing !== 'queued') throw new ChatFailure('refused', 'that message is not waiting', 'state');
    if (m.receipt) return this.write(m.id);
    this.type(m.id);
    return {};
  }
  /** The person's own undo: a message not yet delivered is taken back; a delivered one is closed. */
  cancel(p: Record<string, unknown>) {
    const m = this.find(p.request);
    if (this.sessions.has(m.from) || m.from === m.to) throw new ChatFailure('refused', 'that message is not yours', 'state');
    if (m.landing === 'withdrawn' || m.landing === 'dismissed' || m.landing === 'answered')
      throw new ChatFailure('refused', 'that message is closed', 'state');
    // The agent already has it: closing it tells the agent to stop, so the caller must say it knows.
    const written = m.receipt ? m.receipt.state !== 'queued' : m.landing === 'delivered' || m.landing === 'delivered_unanswered';
    if (written && p.confirm !== true)
      throw new ChatFailure('refused', 'the agent already has this message; cancelling tells it to stop', 'confirm');
    this.change(m, { landing: 'withdrawn', state: 'withdrawn' });
  }
  /** A queued message of a structured agent goes to the CLI: receipt sent, then the CLI's echo on the next step. */
  private write(id: number) {
    const m = this.find(id);
    this.change(m, receiptPatch(m as Msg, 'sent', this.now()));
    this.steps.push(() => this.settle(id, 'confirmed'));
    return { receipt: m.receipt };
  }
  private settle(id: number, state: 'confirmed' | 'unknown') {
    const m = this.live(id);
    if (m?.receipt?.state === 'sent') this.change(m, receiptPatch(m as Msg, state, this.now()));
  }
  /** A structured session's state moved: a turn that ended writes the next held message, a session that ended loses what was not echoed. */
  stateMoved(agent: string, from: string, to: string): void {
    if (!this.sessions.get(agent)?.structured) return;
    const turn = (w: string) => w === 'running' || w === 'waiting';
    if (to === 'parked' || to === 'no_terminal') this.lose(agent);
    else if (turn(from) && to === 'idle') this.turnEnded(agent);
  }
  /** A turn of `agent` ended: the oldest message held behind it is written. */
  turnEnded(agent: string): void {
    const next = this.messages.find((m) => m.agent === agent && m.receipt?.state === 'queued' && m.landing !== 'withdrawn');
    if (next) this.write(next.id);
  }
  /** The daemon or the CLI of `agent` ended: what was written and not echoed is unknown, and is never written again. */
  lose(agent: string): void {
    for (const m of this.messages) if (m.agent === agent) this.settle(m.id, 'unknown');
  }
  /** The stored reply of a retried chat.send with its receipt as it is now; a withdrawn operation is refused. */
  refreshed(r: Record<string, unknown>): Record<string, unknown> {
    const m = r.receipt ? this.messages.find((x) => x.id === r.request) : undefined;
    if (!m) return r;
    if (m.landing === 'withdrawn') throw refused('state', 'that message was withdrawn');
    return { ...r, receipt: m.receipt };
  }
  /** The newest message number the person has seen per agent. */
  readonly seenUpTo = new Map<string, number>();
  seen(p: Record<string, unknown>) {
    if (typeof p.agent !== 'string' || typeof p.up_to !== 'number') throw new ChatFailure('bad_request', 'agent and up_to are needed');
    this.seenUpTo.set(p.agent, Math.max(this.seenUpTo.get(p.agent) ?? 0, p.up_to));
  }
  /** The page of messages around a number, newest first, in the chat that shows it. */
  locate(p: Record<string, unknown>) {
    const m = this.find(p.request);
    const agent = typeof p.agent === 'string' ? p.agent : m.agent;
    const scope = this.sessions.has(m.from) && this.sessions.has(m.to) ? 'all' : 'mine';
    const list = this.messages.filter((x) => x.agent === agent).sort((a, b) => b.id - a.id);
    const at = list.findIndex((x) => x.id === m.id);
    const limit = typeof p.limit === 'number' ? p.limit : 50;
    const from = Math.max(0, at - Math.floor(limit / 2));
    const page = list.slice(from, from + limit);
    return {
      agent,
      scope,
      message: this.view(m),
      messages: page.map((x) => this.view(x)),
      more_older: from + limit < list.length,
      more_newer: from > 0,
    };
  }
  /** One scripted step; false when none is waiting. */
  tick(): boolean {
    const s = this.steps.shift();
    if (s) s();
    return !!s;
  }
}
