// A fake daemon that answers the generated contract (contract/protocol.ts) from fixtures, with one
// scripted fake session per agent. The UI cannot tell it from the real socket; the real daemon is a swap.
// It serves the methods the UI's session screen needs; every other method is refused as `unsupported`.
import crypto from 'node:crypto';
import {
  METHODS,
  PROTOCOL,
  ROLE_ORDER,
  RPC_CODES,
  type Actor,
  type Caller,
  type Event as WireEvent,
  type MethodName,
  type Notifications,
  type Role,
  type Timestamp,
  type Topic,
} from '@handline/contract';
import type { Transport } from '@handline/client';
import { AGENTS, seedChat } from './fixtures.ts';
import { FakeAccounts } from './accounts.ts';
import { FakeAdmin } from './admin.ts';
import { FakeInbox } from './inbox.ts';
import { FakeTasks, taskCall } from './tasks.ts';
import { chatMethod, FakeChat } from './chat.ts';
import { HANDLERS } from './handlers.ts';
import { Script } from './script.ts';
import { Failure, badRequest, refused } from './errors.ts';
import { Knobs } from './knobs.ts';
import { Replies } from './replies.ts';
import { FakeTranscript } from './transcript.ts';
import { watcherNotice } from './watchers.ts';
import { accountsReply, detail, newSession, tid, view, type Attach, type Session } from './session.ts';

const TERMINAL_CAPS = { terminal_format: ['text', 'bytes', 'styled'], terminal_hosts: { fake: ['text', 'bytes', 'styled'] } };
const TEXT_CAPS = { terminal_format: ['text'], terminal_hosts: { fake: ['text'] } };

// Writes whose reply is never replayed: a retry runs again and is judged on its own (a token is handed out once).
const UNKEPT = new Set(['device.pair', 'device.redeem']);

interface Conn {
  transport: Transport;
  role: Role;
  caller: Caller;
  helloed: boolean;
  topics: Set<Topic> | null;
  attached: Map<string, Attach>; // terminal id -> how this connection watches it
  network: boolean; // came in through the paired-device listener
  paired: boolean; // a local connection always; a network one once hello carried a known token
}

export interface FakeDaemonOptions {
  retention?: number; // events kept for resume; older ones give `gap`
  now?: () => Date;
  accounts?: boolean; // serve the account methods (default); false is a daemon that does not yet
  transcript?: boolean; // serve the transcript stream (default); false is a daemon that does not yet
  admin?: boolean; // serve stats, outlook, settings and standing answers (default); false is a daemon that does not yet
  chat?: boolean; // serve the chat methods (default); false is a daemon that does not yet
  inbox?: boolean; // serve the inbox methods (default); false is a daemon that does not yet
  bytes?: boolean; // offer the bytes terminal format (default); false is a daemon that serves text only
}

type Body = { result?: unknown; error?: { code: number; message: string; data: Record<string, unknown> } };

export class FakeDaemon {
  readonly sessions = new Map<string, Session>();
  /** Test controls: `knobs.latency(ms)` and `knobs.refuse(method, gate, reason, hint)`. */
  readonly knobs = new Knobs();
  readonly chat: FakeChat;
  /** The role a paired device gets: a phone is a member; a test or demo that wants the founder's verbs on one sets 'founder'. */
  deviceRole: Role = 'member';
  /** Puts a proposal in as an agent raised it (the hr topic announces it); a demo or test uses this instead of reaching into the script. */
  raiseProposal(p: Parameters<Script['hr']['raise']>[0]) {
    return this.script.hr.raise(p);
  }
  readonly unservedTopics = new Set<Topic>(); // topics `subscribe` refuses, as a daemon that lacks the list does
  readonly inbox: FakeInbox;
  readonly tasks = new FakeTasks(this, () => this.iso());
  readonly accounts: FakeAccounts;
  readonly admin: FakeAdmin;
  /** The agents played as structured sessions and their transcript events; `transcript.push` and `ask` drive the stream. */
  readonly transcript: FakeTranscript;
  private serveChat: boolean;
  private serveBytes: boolean;
  private events: WireEvent[] = [];
  private head = 0;
  private floor = 0; // cursor of the newest event dropped by retention
  private conns = new Set<Conn>();
  private replies = new Replies();
  private timer: ReturnType<typeof setInterval> | null = null;
  private retention: number;
  private now: () => Date;
  private codes = new Set<string>();
  private script = new Script({
    iso: () => this.iso(),
    emit: (topic, type, f) => this.emit(topic, type, f),
    sessions: () => this.sessions,
    broadcast: (s, method, params) => {
      for (const c of this.conns) {
        const a = c.attached.get(tid(s));
        const n = watcherNotice(a, method, params, s.cols);
        if (n) this.notify(c, method, n);
      }
    },
    broadcastBytes: (s, method, build) => {
      for (const c of this.conns) {
        const a = c.attached.get(tid(s));
        if (a?.format === 'bytes') this.notify(c, method, { ...build(), seq: ++a.seq });
      }
    },
  });
  private tokens = new Map<string, { id: string; name: string }>();

  constructor(opts: FakeDaemonOptions = {}) {
    this.retention = opts.retention ?? 1000;
    this.now = opts.now ?? (() => new Date());
    this.serveChat = opts.chat ?? true;
    this.serveBytes = opts.bytes ?? true;
    this.inbox = new FakeInbox(this, () => this.iso(), opts.inbox ?? true);
    this.accounts = new FakeAccounts(this, () => this.iso(), opts.accounts ?? true);
    this.admin = new FakeAdmin(() => this.iso(), opts.admin ?? true);
    this.chat = new FakeChat(
      (type, agent, data) => this.emit('chat', type, { agent, data: data as unknown as Record<string, unknown> }),
      () => this.iso(),
      (n) => this.sessions.has(n),
      (a) => this.sessions.get(a)?.state !== 'parked',
    );
    for (const fx of AGENTS) this.sessions.set(fx.name, newSession(fx, this.iso()));
    this.transcript = new FakeTranscript(this, () => this.iso(), opts.transcript ?? true);
    seedChat(this.chat, this.iso());
  }

  private iso(): Timestamp {
    return this.now().toISOString();
  }

  // ---- hosting ----

  /** Serves one connection; `dropConnections` cuts them. */
  accept(transport: Transport, role: Role = 'founder', name = 'unknown', network = false): void {
    const kind = role === 'agent' ? 'agent' : role === 'member' ? 'device' : 'person';
    const conn: Conn = {
      transport,
      role,
      caller: { kind, name },
      helloed: false,
      topics: null,
      attached: new Map(),
      network,
      paired: !network,
    };
    this.conns.add(conn);
    transport.onmessage = (m) => this.onMessage(conn, m);
    transport.onclose = () => {
      this.conns.delete(conn);
    };
  }

  /** A connection through the network listener: anonymous until hello carries a device token. */
  acceptNetwork(transport: Transport): void {
    this.accept(transport, 'anonymous', 'unknown', true);
  }

  /** What `device.pair` gives the founder: a one-time code to redeem. */
  issueCode(): string {
    const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // the Go daemon's, so a code looks the same
    const code = Array.from(crypto.randomBytes(12), (b, i) => (i && i % 4 === 0 ? '-' : '') + alphabet[b % alphabet.length]).join('');
    this.codes.add(code);
    return code;
  }

  dropConnections(): void {
    for (const c of [...this.conns]) c.transport.close();
  }

  /** Runs the scripted sessions by themselves, one step per interval. */
  autoplay(ms: number): void {
    this.timer = setInterval(() => this.tick(), ms);
  }
  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.dropConnections();
  }

  // ---- the scripted session ----

  /** Runs one scripted step of every session that has one; false when nothing was left to run. */
  tick(): boolean {
    let any = this.chat.tick();
    for (const s of this.sessions.values()) {
      const step = s.queue.shift();
      if (step) {
        step();
        any = true;
      }
    }
    return any;
  }
  /** Runs the script until every session waits for a person or is idle. */
  settle(): void {
    while (this.tick());
  }

  /** The daemon's own typing: refused while a person holds the terminal, marked in the stream otherwise. */
  daemonTypes(agent: string, text: string): void {
    const s = this.session(agent);
    if (s.holder.kind !== 'daemon') throw refused('input_holder', `${s.holder.name} holds the input`);
    this.script.record(s, 'text', text, 'daemon');
    this.script.edit(s, () => {
      s.lines.push(`> ${text}`);
    });
    this.script.pushTerminal(s, 'marker', (seq): Notifications['marker'] => ({
      terminal: tid(s),
      seq,
      at: this.iso(),
      kind: 'daemon_typed',
      actor: { kind: 'daemon', name: 'daemon' },
      text,
      format: 'text',
    }));
  }

  /** Makes the bytes watchers of an agent fall behind: they are told to repaint, as the daemon does past 1 MiB unsent. */
  slowClient(agent: string): void {
    this.script.snapshotBytes(this.session(agent), 'slow_client');
  }

  private session(agent: unknown): Session {
    const s = typeof agent === 'string' ? this.sessions.get(agent) : undefined;
    if (!s) throw new Failure('not_found', `no agent named ${String(agent)}`);
    return s;
  }
  // ---- events ----

  /** Public so a test can make changes while no client is connected. */
  emit(topic: Topic, type: string, f: { agent?: string; actor?: Actor; title?: string; data?: Record<string, unknown> } = {}) {
    const e: WireEvent = {
      cursor: ++this.head,
      topic,
      type,
      at: this.iso(),
      actor: f.actor ?? null,
      agent: f.agent ?? null,
      title: f.title ?? null,
      target: f.agent ? { screen: 'agent', id: f.agent } : null,
      data: f.data ?? {},
    };
    this.events.push(e);
    while (this.events.length > this.retention) this.floor = this.events.shift()!.cursor;
    for (const c of this.conns) if (c.topics?.has(topic)) this.notify(c, 'event', e);
  }

  // ---- wire ----

  private notify(c: Conn, method: string, params: unknown) {
    c.transport.send(JSON.stringify({ jsonrpc: '2.0', method, params }));
  }
  private reply(c: Conn, id: unknown, body: Body) {
    c.transport.send(JSON.stringify({ jsonrpc: '2.0', id, ...body }));
  }
  private fail(e: Failure): Body {
    return { error: { code: RPC_CODES[e.code] ?? -32603, message: e.message, data: e.data } };
  }

  private onMessage(c: Conn, line: string) {
    let req: { id?: number | string; method?: string; params?: Record<string, unknown> };
    try {
      req = JSON.parse(line);
    } catch {
      return this.reply(c, null, { error: { code: -32700, message: 'The request is not valid JSON.', data: { code: 'bad_request' } } });
    }
    if (typeof req.method !== 'string' || req.id === undefined) {
      return this.reply(c, req.id ?? null, this.fail(badRequest('A request needs an id and a method.')));
    }
    const params = req.params ?? {};
    if (
      c.network &&
      !c.paired &&
      !(req.method === 'device.redeem' || (req.method === 'hello' && typeof params.token === 'string' && this.tokens.has(params.token)))
    ) {
      // The listener's one reply to anyone unpaired, the same words whatever was asked.
      return this.reply(c, req.id, this.fail(new Failure('refused', 'refused', { gate: 'unpaired', reason: 'not paired' })));
    }
    const { id, method } = req;
    this.knobs.defer(method, () => this.handle(c, id, method, params));
  }

  private handle(c: Conn, id: number | string, method: string, params: Record<string, unknown>) {
    let body: Body;
    try {
      const no = c.helloed ? this.knobs.check(method) : undefined;
      if (no) throw no;
      body = { result: this.withReplay(c, method, params) };
    } catch (e) {
      if (!(e instanceof Failure)) throw e;
      body = this.fail(e);
    }
    this.reply(c, id, body);
    if (method === 'subscribe' && !body.error) this.replay(c, params.since as number | undefined);
  }

  /** A write's successful reply is kept by request_id; the same id with the same params returns it, repeating nothing. */
  private withReplay(c: Conn, method: string, p: Record<string, unknown>): unknown {
    const spec = METHODS[method as MethodName];
    if (!spec?.write || typeof p.request_id !== 'string' || UNKEPT.has(method)) return this.dispatch(c, method, p);
    return this.replies.once(p, () => this.dispatch(c, method, p) as Record<string, unknown>);
  }

  // Replayed events follow the subscribe reply, never precede it.
  private replay(c: Conn, since: number | undefined) {
    if (since === undefined) return;
    if (since < this.floor) {
      this.notify(c, 'gap', { reason: 'retention', cursor: this.head } satisfies Notifications['gap']);
      return;
    }
    for (const e of this.events) if (e.cursor > since && c.topics?.has(e.topic)) this.notify(c, 'event', e);
  }

  private actor(c: Conn): Actor {
    return { kind: c.caller.kind === 'device' ? 'device' : c.caller.kind === 'agent' ? 'agent' : 'user', name: c.caller.name };
  }

  private dispatch(c: Conn, method: string, p: Record<string, unknown>): unknown {
    const spec = METHODS[method as MethodName];
    if (method === 'hello') {
      if (typeof p.client !== 'string') throw badRequest('hello needs client', 'client');
      if (typeof p.protocol !== 'number') throw badRequest('hello needs protocol', 'protocol');
      const device = c.network && typeof p.token === 'string' ? this.tokens.get(p.token) : undefined;
      if (device) {
        c.role = this.deviceRole;
        c.paired = true;
        c.caller = { kind: 'device', device: device.id, name: device.name };
      }
      c.helloed = true;
      if (c.caller.name === 'unknown') c.caller = { ...c.caller, name: p.client };
      return {
        protocol: PROTOCOL,
        min_client: 1,
        daemon: 'fake0000',
        schema: 1,
        role: c.role,
        caller: c.caller,
        capabilities: { chat: this.serveChat, transcript: this.transcript.on, review: false, input_ask: false, ...(this.serveBytes ? TERMINAL_CAPS : TEXT_CAPS) },
        transport: c.network ? 'network' : 'local',
        methods: Object.entries(METHODS)
          .filter(([name]) => this.served.every((m) => m.lists(name)))
          .map(([name, m]) => ({
            name,
            since: m.since,
            role: m.role,
            allowed: this.mayCall(c.role, m.role),
          })),
      };
    }
    if (!c.helloed && method !== 'device.redeem') throw badRequest('say hello first'); // an unpaired caller redeems with no hello
    if (!spec) throw new Failure('not_found', `unknown method ${method}`);
    if (!this.mayCall(c.role, spec.role)) throw refused('role', `the ${c.role} role may not call ${method}`);
    if (spec.write && typeof p.request_id !== 'string') throw badRequest(`${method} needs a request_id`, 'request_id');
    if (!this.served.every((m) => m.lists(method))) throw refused('unsupported', `the fake daemon does not serve ${method}`);
    const actor = this.actor(c);
    const agent = () => this.session(p.agent);
    const ok = (said: string, extra = {}) => ({ ...this.script.accepted(said), ...extra });
    if (method.startsWith('chat.')) return chatMethod(this.chat, method, p, c.caller.name, agent, ok, (id) => this.inbox.dismiss(id));
    const owned = this.moduleCall(c, method, p, ok);
    if (owned) return owned.value;
    const handler = HANDLERS[method];
    if (handler)
      return handler({ c, p, actor, script: this.script, transcript: this.transcript, agent, byTerminal: (id) => this.byTerminal(id), iso: () => this.iso() });
    switch (method as MethodName) {
      case 'whoami':
        return {
          caller: c.caller,
          role: c.role,
          methods: Object.entries(METHODS)
            .filter(([, m]) => this.mayCall(c.role, m.role))
            .map(([n]) => n),
        };
      case 'daemon.status': {
        const by_state: Record<string, number> = {};
        for (const s of this.sessions.values()) by_state[s.state] = (by_state[s.state] ?? 0) + 1;
        return {
          alive: true,
          version: 'fake0000',
          protocol: PROTOCOL,
          schema: 1,
          started_at: this.iso(),
          terminal_host: { name: 'fake', health: 'ok' },
          agents: { total: this.sessions.size, by_state },
          inbox: { unread: this.inbox.unread() },
        };
      }
      case 'agents.list':
        return { agents: [...this.sessions.values()].map((s) => view(s)) };
      case 'agent.get':
        return detail(agent());
      case 'subscribe': {
        if (!Array.isArray(p.topics) || !p.topics.length) throw badRequest('topics must be a non-empty list', 'topics');
        for (const t of p.topics as Topic[]) if (this.unservedTopics.has(t)) throw badRequest(`no such topic: ${t}`, 'topics');
        c.topics = new Set(this.transcript.gate(p.topics as Topic[], c.role));
        return { cursor: this.head };
      }
      case 'unsubscribe':
        for (const t of (p.topics as Topic[] | undefined) ?? []) c.topics?.delete(t);
        return {};
      case 'prompt.list': {
        const s = agent();
        return { prompts: s.prompt ? [s.prompt] : [] };
      }
      case 'device.pair': {
        const code = this.issueCode();
        return this.script.accepted('Pairing code made.', {
          code,
          expires_at: new Date(this.now().getTime() + 300_000).toISOString(),
          fingerprint: 'fa'.repeat(32),
          uri: `handline://pair?${new URLSearchParams({ host: '127.0.0.1:0', fp: 'fa'.repeat(32), code })}`,
        });
      }
      case 'device.redeem': {
        if (typeof p.code !== 'string') throw badRequest('code is a string', 'code');
        if (typeof p.name !== 'string' || p.name === '') throw badRequest("name is the device's name, a string", 'name');
        if (!this.codes.delete(p.code.trim().toUpperCase())) throw refused('pairing_code', 'that pairing code is not valid');
        const token = 'hld_' + crypto.randomBytes(32).toString('hex'),
          id = 'dev_' + crypto.randomBytes(6).toString('hex');
        this.tokens.set(token, { id, name: p.name });
        return this.script.accepted('Paired.', { token, device: id });
      }
      default:
        throw refused('unsupported', `the fake daemon does not serve ${method}`);
    }
  }

  /** Everything that may leave a method out of what this daemon lists and serves. */
  private get served(): { lists(name: string): boolean }[] {
    return [this.tasks, this.inbox, this.accounts, this.admin, this.transcript, this.knobs, { lists: (n) => this.serveChat || !n.startsWith('chat.') }];
  }

  /** The fake modules' methods (accounts, stats and settings, inbox, tasks); null for a method none of them owns. */
  private moduleCall(c: Conn, method: string, p: Record<string, unknown>, ok: (said: string, extra?: object) => object) {
    if (method === 'accounts.list' && this.knobs.accounts)
      return { value: accountsReply(this.knobs.accounts, [...this.sessions.values()]) };
    if (/^accounts?\./.test(method)) return { value: this.accounts.call(method, p, ok) };
    if (this.admin.owns(method)) return { value: this.admin.call(method, p, ok) };
    if (method.startsWith('inbox.')) return { value: this.inbox.call(method, p, c.caller.name, ok) };
    if (this.tasks.serves(method)) return { value: taskCall(this.tasks, method, p, c.caller.name, this.script) };
    return null;
  }

  private byTerminal(id: unknown): Session {
    const s = [...this.sessions.values()].find((x) => tid(x) === id);
    if (!s) throw new Failure('not_found', `no terminal ${String(id)}`);
    return s;
  }

  private mayCall(role: Role, lowest: Role): boolean {
    return ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(lowest);
  }
}
