// A fake daemon that answers the generated contract (contract/protocol.ts) from fixtures, with one
// scripted fake session per agent. The UI cannot tell it from the real socket; the real daemon is a swap.
// It serves the methods the UI's session screen needs; every other method is refused as `unsupported`.
import {
  METHODS,
  PROTOCOL,
  ROLE_ORDER,
  type Actor,
  type Event as WireEvent,
  type MethodName,
  type Notifications,
  type Role,
  type Timestamp,
  type Topic,
} from '@handline/contract';
import type { Transport } from '@handline/client';
import { AGENTS, seedChat } from '#src/fixtures.ts';
import { FakeAccounts } from '#src/accounts.ts';
import { FakeAdmin } from '#src/admin.ts';
import { FakePairing } from '#src/pairing.ts';
import { FakeInbox } from '#src/inbox.ts';
import { FakeWork } from '#src/workroom.ts';
import { FakeRequests } from '#src/requests.ts';
import { FakeTasks } from '#src/tasks.ts';
import { taskCall } from '#src/taskcall.ts';
import { FakeAttachments } from '#src/attachments.ts';
import { chatMethod, FakeChat } from '#src/chat.ts';
import { HANDLERS } from '#src/handlers.ts';
import { Script } from '#src/script.ts';
import { Failure, badRequest, refused } from '#src/errors.ts';
import { Knobs } from '#src/knobs.ts';
import { TERMINAL_CAPS, TEXT_CAPS, UNKEPT, failBody, type Body, type Conn, type FakeDaemonOptions } from '#src/conn.ts';

export type { FakeDaemonOptions };
export type { FakeHr } from '#src/hr.ts';
import { Replies } from '#src/replies.ts';
import { FakeTranscript } from '#src/transcript.ts';
import { watcherNotice } from '#src/watchers.ts';
import { stamped, subscribeTopics } from '#src/resume.ts';
import { accountsReply, byTerminal, detail, statusReply, newSession, tid, view, type Session } from '#src/session.ts';

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
  private serveChat: boolean;
  private serveBytes: boolean;
  private events: WireEvent[] = [];
  private head = 0;
  private floor = 0; // cursor of the newest event dropped by retention
  private conns = new Set<Conn>();
  private replies = new Replies();
  readonly uploads = new FakeAttachments();
  private timer: ReturnType<typeof setInterval> | null = null;
  private retention: number;
  private now: () => Date;
  private pairing = new FakePairing();
  readonly script = new Script({
    iso: () => this.iso(),
    emit: (topic, type, f) => this.emit(topic, type, f),
    sessions: () => this.sessions,
    moved: (s, from) => this.chat.stateMoved(s.fx.name, from, s.state),
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
    watched: (s) => [...this.conns].some((c) => c.attached.has(tid(s))),
  });

  /** The founder's reply to an ask a comment raised: a comment on its task, a reply to that comment. */
  answerAsk(task: number, memo: number, text: string): void {
    this.tasks.memo({ task, text, reply: memo }, 'add', 'human');
  }

  constructor(opts: FakeDaemonOptions = {}) {
    this.retention = opts.retention ?? 1000;
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

  private iso = (): Timestamp => this.now().toISOString();

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
    if (!s) throw new Failure('not_found', `no agent named ${String(agent)}`, { what: 'agent', name: agent });
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
    if (type === 'agent.removed' && f.agent) this.admin.prefs.forget(f.agent);
  }

  // ---- wire ----

  private notify(c: Conn, method: string, params: unknown) {
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
      body = { result: stamped(method, this.head, () => this.withReplay(c, method, params)) };
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
    if (since < this.floor) {
      this.notify(c, 'gap', { reason: 'retention', cursor: this.head } satisfies Notifications['gap']);
      return;
    }
    for (const e of this.events) if (e.cursor > since && c.topics?.has(e.topic)) this.notify(c, 'event', e);
  }

  private actor(c: Conn): Actor {
    return { kind: c.caller.kind === 'device' ? 'device' : c.caller.kind === 'agent' ? 'agent' : 'user', name: c.caller.name };
  }

  private hello(c: Conn, p: Record<string, unknown>): unknown {
    if (typeof p.client !== 'string') throw badRequest('hello needs client', 'client');
    if (typeof p.protocol !== 'number') throw badRequest('hello needs protocol', 'protocol');
    const device = c.network ? this.pairing.device(p.token) : undefined;
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
      capabilities: {
        chat: this.serveChat,
        transcript: this.transcript.on,
        review: false,
        ...(this.serveBytes ? TERMINAL_CAPS : TEXT_CAPS),
      },
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

  private dispatch(c: Conn, method: string, p: Record<string, unknown>): unknown {
    const spec = METHODS[method as MethodName];
    if (method === 'hello') return this.hello(c, p);
    if (!c.helloed && method !== 'device.redeem') throw badRequest('say hello first'); // an unpaired caller redeems with no hello
    if (!spec) throw new Failure('not_found', `unknown method ${method}`);
    if (!this.mayCall(c.role, spec.role)) throw refused('role', `the ${c.role} role may not call ${method}`);
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
    const owned = this.moduleCall(c, method, p, ok);
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
    switch (method as MethodName) {
      case 'whoami':
        return {
          caller: c.caller,
          role: c.role,
          methods: Object.entries(METHODS)
            .filter(([, m]) => this.mayCall(c.role, m.role))
            .map(([n]) => n),
        };
      case 'daemon.status':
        return statusReply(this.sessions, this.inbox.unread(), this.iso());
      case 'agents.list':
        return { agents: [...this.sessions.values()].map((s) => view(s)) };
      case 'agent.get':
        return detail(agent());
      case 'pr.exists': {
        const repo = agent().fx.repo ?? null;
        const nums = p.numbers;
        if (!Array.isArray(nums) || nums.length > 20 || !nums.every((n) => Number.isInteger(n)))
          throw badRequest('numbers must be at most 20 integers', 'numbers');
        return { repo, found: repo ? (nums as number[]).filter((n) => this.pullRequests.has(n)) : [] };
      }
      case 'subscribe': {
        c.topics = new Set(this.transcript.gate(subscribeTopics(p, this.unservedTopics), c.role));
        return { cursor: this.head };
      }
      case 'unsubscribe':
        for (const t of (p.topics as Topic[] | undefined) ?? []) c.topics?.delete(t);
        return {};
      case 'prompt.list': {
        const s = agent();
        return { prompts: s.prompt ? [s.prompt] : [] };
      }
      case 'device.pair':
        return this.pairing.pair(ok, this.now());
      case 'device.redeem':
        return this.pairing.redeem(ok, p);
      default:
        throw refused('unsupported', `the fake daemon does not serve ${method}`);
    }
  }

  /** Everything that may leave a method out of what this daemon lists and serves. */
  private get served(): { lists(name: string): boolean }[] {
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

  /** The fake modules' methods (accounts, stats and settings, inbox, tasks); null for a method none of them owns. */
  private moduleCall(c: Conn, method: string, p: Record<string, unknown>, ok: (said: string, extra?: object) => object) {
    if (method === 'accounts.list' && this.knobs.accounts)
      return { value: accountsReply(this.knobs.accounts, [...this.sessions.values()]) };
    if (/^accounts?\./.test(method)) return { value: this.accounts.call(method, p, ok) };
    if (this.admin.owns(method)) {
      const value = this.admin.call(method, p, ok);
      if (method === 'device.revoke') this.emit('devices', 'device.revoked', { data: { device: p.device } });
      return { value };
    }
    if (this.work.owns(method)) return { value: this.work.call(method, p, ok) };
    if (this.requests.owns(method)) return { value: this.requests.call(method, p, ok) };
    if (method.startsWith('attachment.')) return { value: this.uploads.call(method, p, ok) };
    if (method.startsWith('inbox.')) return { value: this.inbox.call(method, p, c.caller.name, ok) };
    if (this.tasks.serves(method))
      return {
        value: taskCall(this.tasks, method, p, c.caller.name, c.role === 'agent', this.script, (task, to, text) => {
          const s = this.session(to);
          if (!s.open) this.script.startSession(s, false, this.actor(c));
          const body =
            `Question on T${task} from the founder (the task keeps its holder): ${text}\n\n` +
            "Answer by replying to this message: hl say human --parent <this message's #id> '<answer>'";
          const { request } = this.chat.send({ agent: to, body, request_id: p.request_id }, c.caller.name);
          this.chat.link(request, { id: task, title: this.tasks.find(task).title });
          return request;
        }),
      };
    return null;
  }

  private mayCall(role: Role, lowest: Role): boolean {
    return ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(lowest);
  }
}
