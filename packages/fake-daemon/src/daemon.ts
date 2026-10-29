// A fake daemon that answers the generated contract (contract/protocol.ts) from fixtures, with one
// scripted fake session per agent. The UI cannot tell it from the real socket; the real daemon is a swap.
// It serves the methods the UI's session screen needs; every other method is refused as `unsupported`.
import { METHODS, type Actor, type MethodName, type Notifications, type Role, type Timestamp, type Topic } from '@handline/contract';
import type { Transport } from '@handline/client';
import { AGENTS, seedChat } from '#src/fixtures.ts';
import { FakeAccounts } from '#src/accounts.ts';
import { FakeAdmin } from '#src/admin.ts';
import { FakePairing } from '#src/pairing.ts';
import { FakeInbox } from '#src/inbox.ts';
import { FakeWork } from '#src/workroom.ts';
import { FakeRequests } from '#src/requests.ts';
import { FakeTasks } from '#src/tasks.ts';
import { FakeAttachments } from '#src/attachments.ts';
import { FakeChat } from '#src/chat.ts';
import { chatMethod } from '#src/chatcall.ts';
import { HANDLERS } from '#src/handlers.ts';
import { Script } from '#src/script.ts';
import { scriptHost } from '#src/scripthost.ts';
import { Failure, badRequest, refused } from '#src/errors.ts';
import { Knobs } from '#src/knobs.ts';
import { UNKEPT, failBody, type Body, type Conn, type FakeDaemonOptions } from '#src/conn.ts';

export type { FakeDaemonOptions };
export type { FakeHr } from '#src/hr.ts';
import { Replies } from '#src/replies.ts';
import { FakeTranscript } from '#src/transcript.ts';
import { stamped } from '#src/resume.ts';
import { byTerminal, newSession, type Session } from '#src/session.ts';
import { EventLog } from '#src/eventlog.ts';
import { coreCall, hello, mayCall } from '#src/core.ts';
import { moduleCall } from '#src/modules.ts';

export class FakeDaemon {
  readonly sessions = new Map<string, Session>();
  /** Test controls: `knobs.latency(ms)` and `knobs.refuse(method, gate, reason, hint)`. */
  readonly knobs = new Knobs();
  readonly chat: FakeChat;
  /** The role a paired device gets: the founder's, as the real daemon gives it; a test that wants a restricted device sets 'member'. */
  deviceRole: Role = 'founder';
  /** The numbers pr.exists reports as pull requests, for every agent. */
  readonly pullRequests = new Set<number>();
  /** Puts a proposal in as an agent raised it (the hr topic announces it); a demo or test uses this instead of reaching into the script. */
  raiseProposal(p: Parameters<Script['hr']['raise']>[0]) {
    return this.script.hr.raise(p);
  }
  readonly unservedTopics = new Set<Topic>(); // topics `subscribe` refuses, as a daemon that lacks the list does
  readonly inbox: FakeInbox;
  readonly tasks = new FakeTasks(this, () => this.iso());
  /** The founder's requests (requests.list) and task.request / task.none; `requests.add` puts a row in. */
  readonly requests = new FakeRequests(this, () => this.iso());
  readonly accounts: FakeAccounts;
  readonly admin: FakeAdmin;
  /** The repos, worktrees and the agent.create operation; `work.stepMs` slows its steps. */
  readonly work = new FakeWork(this, () => this.iso());
  /** The agents played as structured sessions and their transcript events; `transcript.push` and `ask` drive the stream. */
  readonly transcript: FakeTranscript;
  readonly serveChat: boolean;
  readonly serveBytes: boolean;
  readonly log: EventLog;
  readonly conns = new Set<Conn>();
  private replies = new Replies();
  readonly uploads = new FakeAttachments();
  private timer: ReturnType<typeof setInterval> | null = null;
  readonly now: () => Date;
  readonly pairing = new FakePairing();
  readonly script = new Script(scriptHost(this));

  constructor(opts: FakeDaemonOptions = {}) {
    this.log = new EventLog(opts.retention ?? 1000);
    this.now = opts.now ?? (() => new Date());
    this.serveChat = opts.chat ?? true;
    this.serveBytes = opts.bytes ?? true;
    this.inbox = new FakeInbox(this, () => this.iso(), opts.inbox ?? true);
    this.accounts = new FakeAccounts(this, () => this.iso(), opts.accounts ?? true);
    this.admin = new FakeAdmin(() => this.iso(), opts.admin ?? true, this);
    this.chat = new FakeChat(
      (type, agent, data) => this.emit('chat', type, { agent, data: data as unknown as Record<string, unknown> }),
      () => this.iso(),
      this.sessions,
    );
    for (const fx of AGENTS) this.sessions.set(fx.name, newSession(fx, this.iso()));
    this.transcript = new FakeTranscript(this, () => this.iso(), opts.transcript ?? true);
    seedChat(this.chat, this.iso());
  }

  readonly iso = (): Timestamp => this.now().toISOString();

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
    transport.onclose = () => void (this.conns.delete(conn), this.script.gone(conn.attached));
  }

  /** A connection through the network listener: anonymous until hello carries a device token. */
  acceptNetwork(transport: Transport): void {
    this.accept(transport, 'anonymous', 'unknown', true);
  }

  /** What `device.pair` gives the founder: a one-time code to redeem. */
  issueCode(): string {
    return this.pairing.issueCode();
  }

  dropConnections(): void {
    for (const c of [...this.conns]) c.transport.close();
  }

  /** Runs the scripted sessions by themselves, one step per interval. */
  autoplay(ms: number): void {
    this.timer = setInterval(() => this.tick(), ms);
  }
  stop(): void {
    clearInterval(this.timer ?? undefined);
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
    this.script.daemonTypes(this.session(agent), text);
  }

  /** Makes the bytes watchers of an agent fall behind: they are told to repaint, as the daemon does past 1 MiB unsent. */
  slowClient(agent: string): void {
    this.script.snapshotBytes(this.session(agent), 'slow_client');
  }

  session(agent: unknown): Session {
    const s = typeof agent === 'string' ? this.sessions.get(agent) : undefined;
    if (!s) throw new Failure('not_found', `no agent named ${String(agent)}`, { what: 'agent', name: agent });
    return s;
  }
  // ---- events ----

  /** Public so a test can make changes while no client is connected. */
  emit(topic: Topic, type: string, f: { agent?: string; actor?: Actor; title?: string; data?: Record<string, unknown> } = {}) {
    const e = this.log.append({
      topic,
      type,
      at: this.iso(),
      actor: f.actor ?? null,
      agent: f.agent ?? null,
      title: f.title ?? null,
      target: f.agent ? { screen: 'agent', id: f.agent } : null,
      data: f.data ?? {},
    });
    for (const c of this.conns) if (c.topics?.has(topic)) this.notify(c, 'event', e);
    if (type === 'agent.removed' && f.agent) this.admin.prefs.forget(f.agent);
  }

  // ---- wire ----

  notify(c: Conn, method: string, params: unknown) {
    c.transport.send(JSON.stringify({ jsonrpc: '2.0', method, params }));
  }
  private reply(c: Conn, id: unknown, body: Body) {
    c.transport.send(JSON.stringify({ jsonrpc: '2.0', id, ...body }));
  }

  private onMessage(c: Conn, line: string) {
    let req: { id?: number | string; method?: string; params?: Record<string, unknown> };
    try {
      req = JSON.parse(line);
    } catch {
      return this.reply(c, null, { error: { code: -32700, message: 'The request is not valid JSON.', data: { code: 'bad_request' } } });
    }
    if (typeof req.method !== 'string' || req.id === undefined) {
      return this.reply(c, req.id ?? null, failBody(badRequest('A request needs an id and a method.')));
    }
    const params = req.params ?? {};
    if (c.network && !c.paired && !(req.method === 'device.redeem' || (req.method === 'hello' && this.pairing.knows(params.token)))) {
      // The listener's one reply to anyone unpaired, the same words whatever was asked.
      return this.reply(c, req.id, failBody(new Failure('refused', 'refused', { gate: 'unpaired', reason: 'not paired' })));
    }
    const { id, method } = req;
    this.knobs.defer(method, () => this.handle(c, id, method, params));
  }

  private handle(c: Conn, id: number | string, method: string, params: Record<string, unknown>) {
    let body: Body;
    try {
      const no = c.helloed ? this.knobs.check(method) : undefined;
      if (no) throw no;
      body = { result: stamped(method, this.log.head, () => this.withReplay(c, method, params)) };
    } catch (e) {
      if (!(e instanceof Failure)) throw e;
      body = failBody(e);
    }
    this.reply(c, id, body);
    if (method === 'subscribe' && !body.error && params.resume !== 'latest') this.replay(c, params.since as number | undefined);
  }

  /** A write's successful reply is kept by request_id; the same id with the same params returns it, repeating nothing. */
  private withReplay(c: Conn, method: string, p: Record<string, unknown>): unknown {
    const spec = METHODS[method as MethodName];
    if (!spec?.write || typeof p.request_id !== 'string' || UNKEPT.has(method)) return this.dispatch(c, method, p);
    return this.replies.once(
      p,
      () => this.dispatch(c, method, p) as Record<string, unknown>,
      (r) => this.chat.refreshed(r),
    );
  }

  // Replayed events follow the subscribe reply, never precede it.
  private replay(c: Conn, since: number | undefined) {
    if (since === undefined) return;
    const missed = this.log.after(since);
    if (!missed) this.notify(c, 'gap', { reason: 'retention', cursor: this.log.head } satisfies Notifications['gap']);
    else for (const e of missed) if (c.topics?.has(e.topic)) this.notify(c, 'event', e);
  }

  actor(c: Conn): Actor {
    return { kind: c.caller.kind === 'device' ? 'device' : c.caller.kind === 'agent' ? 'agent' : 'user', name: c.caller.name };
  }

  private dispatch(c: Conn, method: string, p: Record<string, unknown>): unknown {
    const spec = METHODS[method as MethodName];
    if (method === 'hello') return hello(this, c, p);
    if (!c.helloed && method !== 'device.redeem') throw badRequest('say hello first'); // an unpaired caller redeems with no hello
    if (!spec) throw new Failure('not_found', `unknown method ${method}`);
    if (!mayCall(c.role, spec.role)) throw refused('role', `the ${c.role} role may not call ${method}`);
    if (spec.write && typeof p.request_id !== 'string') throw badRequest(`${method} needs a request_id`, 'request_id');
    if (!this.served.every((m) => m.lists(method))) throw refused('unsupported', `the fake daemon does not serve ${method}`);
    const actor = this.actor(c);
    const agent = () => this.session(p.agent);
    const ok = (said: string, extra = {}) => ({ ...this.script.accepted(said), ...extra });
    if (method === 'chat.send') {
      // A message to an agent with no open session starts one: nobody wakes an agent by hand.
      const s = agent();
      if (!s.open) this.script.startSession(s, false, actor);
    }
    if (method.startsWith('chat.')) return chatMethod(this.chat, method, p, c.caller.name, agent, ok, this.uploads);
    const owned = moduleCall(this, c, method, p, ok);
    if (owned) return owned.value;
    const handler = HANDLERS[method];
    if (handler)
      return handler({
        c,
        p,
        actor,
        script: this.script,
        transcript: this.transcript,
        agent,
        byTerminal: (id) => byTerminal(new Map([...this.sessions, ...this.script.shells]), id),
        iso: () => this.iso(),
      });
    return coreCall(this, c, method, p, ok, agent);
  }

  /** Everything that may leave a method out of what this daemon lists and serves. */
  get served(): { lists(name: string): boolean }[] {
    return [
      this.tasks,
      this.requests,
      this.inbox,
      this.accounts,
      this.admin,
      this.transcript,
      this.knobs,
      this.work,
      { lists: (n) => this.serveChat || !n.startsWith('chat.') },
    ];
  }
}
