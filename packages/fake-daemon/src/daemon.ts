// A fake daemon that answers the generated contract (contract/protocol.ts) from fixtures, with one
// scripted fake session per agent. The UI cannot tell it from the real socket; the real daemon is a swap.
// It serves the methods the UI's session screen needs; every other method is refused as `unsupported`.
import crypto from 'node:crypto';
import {
  METHODS, PROTOCOL, ROLE_ORDER, RPC_CODES,
  type Actor, type AgentDetail, type AgentView, type Caller, type ErrorCode, type Event as WireEvent,
  type InputHolder, type MethodName, type Notifications, type Prompt, type Role, type Row, type Timestamp, type Topic,
} from '@handline/contract';
import type { Transport } from '@handline/client';
import { AGENTS, BANNER, PROMPT_LINES, type AgentFixture } from './fixtures.ts';

const ROWS = 24, COLS = 80;
const KEYS = new Set(['esc', 'enter', 'up', 'down', 'tab', 'shift-tab', 'ctrl-c', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']);
const DAEMON_HOLDER: InputHolder = { kind: 'daemon', name: 'daemon', since: null };
const row = (text: string): Row => ({ text });

/** A refusal or error the caller gets as a JSON-RPC error. */
class Failure extends Error {
  code: ErrorCode;
  data: Record<string, unknown>;
  constructor(code: ErrorCode, message: string, data: Record<string, unknown> = {}) {
    super(message); this.code = code; this.data = { code, ...data };
  }
}
const refused = (gate: string, reason: string, hint?: string) => new Failure('refused', reason, { reason, gate, hint });
const badRequest = (message: string, field?: string) => new Failure('bad_request', message, { field });

interface Session {
  fx: AgentFixture;
  state: string;
  since: Timestamp;
  sessionId: string | null;
  open: boolean;
  hasTerminal: boolean;
  lines: string[];
  seq: number;
  cols: number;
  rows: number;
  buffer: string; // typed, Enter not yet pressed
  composing: boolean; // the last line shows the buffer
  holder: InputHolder;
  prompt: Prompt | null;
  queue: (() => void)[]; // the scripted turn, one step per tick
  counter: number;
  received: { kind: 'text' | 'key'; value: string; actor: string }[]; // what reached the terminal
}

interface Conn {
  transport: Transport;
  role: Role;
  caller: Caller;
  helloed: boolean;
  topics: Set<Topic> | null;
  attached: Set<string>; // terminal ids
  network: boolean; // came in through the paired-device listener
}

export interface FakeDaemonOptions {
  retention?: number; // events kept for resume; older ones give `gap`
  now?: () => Date;
}

type Body = { result?: unknown; error?: { code: number; message: string; data: Record<string, unknown> } };

export class FakeDaemon {
  readonly sessions = new Map<string, Session>();
  private events: WireEvent[] = [];
  private head = 0;
  private floor = 0; // cursor of the newest event dropped by retention
  private conns = new Set<Conn>();
  private replies = new Map<string, { params: string; result: Record<string, unknown> }>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private retention: number;
  private now: () => Date;
  private timelineId = 0;
  private codes = new Set<string>();
  private tokens = new Map<string, string>(); // device token -> device name

  constructor(opts: FakeDaemonOptions = {}) {
    this.retention = opts.retention ?? 1000;
    this.now = opts.now ?? (() => new Date());
    for (const fx of AGENTS) this.sessions.set(fx.name, this.newSession(fx));
  }

  private iso(): Timestamp { return this.now().toISOString(); }

  private newSession(fx: AgentFixture): Session {
    const open = fx.session !== null;
    return {
      fx, state: fx.state, since: this.iso(), sessionId: fx.session, open, hasTerminal: fx.state !== 'no_terminal',
      lines: open ? BANNER.map((l) => l.replace('{worktree}', fx.worktree)) : [], seq: 0, cols: COLS, rows: ROWS,
      buffer: '', composing: false, holder: DAEMON_HOLDER, prompt: null, queue: [], counter: 0, received: [],
    };
  }

  // ---- hosting ----

  /** Serves one connection; `dropConnections` cuts them. */
  accept(transport: Transport, role: Role = 'founder', name = 'unknown', network = false): void {
    const kind = role === 'agent' ? 'agent' : role === 'member' ? 'device' : 'person';
    const conn: Conn = { transport, role, caller: { kind, name }, helloed: false, topics: null, attached: new Set(), network };
    this.conns.add(conn);
    transport.onmessage = (m) => this.onMessage(conn, m);
    transport.onclose = () => { this.conns.delete(conn); };
  }

  /** A connection through the network listener: anonymous until hello carries a device token. */
  acceptNetwork(transport: Transport): void { this.accept(transport, 'anonymous', 'unknown', true); }

  /** What `device.pair` gives the founder: a one-time code to redeem. */
  issueCode(): string { const code = crypto.randomBytes(5).toString('hex').toUpperCase(); this.codes.add(code); return code; }

  dropConnections(): void { for (const c of [...this.conns]) c.transport.close(); }

  /** Runs the scripted sessions by themselves, one step per interval. */
  autoplay(ms: number): void { this.timer = setInterval(() => this.tick(), ms); }
  stop(): void { if (this.timer) clearInterval(this.timer); this.timer = null; this.dropConnections(); }

  // ---- the scripted session ----

  /** Runs one scripted step of every session that has one; false when nothing was left to run. */
  tick(): boolean {
    let any = false;
    for (const s of this.sessions.values()) {
      const step = s.queue.shift();
      if (step) { step(); any = true; }
    }
    return any;
  }
  /** Runs the script until every session waits for a person or is idle. */
  settle(): void { while (this.tick()); }

  /** The daemon's own typing: refused while a person holds the terminal, marked in the stream otherwise. */
  daemonTypes(agent: string, text: string): void {
    const s = this.session(agent);
    if (s.holder.kind !== 'daemon') throw refused('input_holder', `${s.holder.name} holds the input`);
    this.record(s, 'text', text, 'daemon');
    this.edit(s, () => { s.lines.push(`> ${text}`); });
    this.pushTerminal(s, 'marker', (seq): Notifications['marker'] => ({
      terminal: tid(s), seq, at: this.iso(), kind: 'daemon_typed', actor: { kind: 'daemon', name: 'daemon' }, text }));
  }

  private session(agent: unknown): Session {
    const s = typeof agent === 'string' ? this.sessions.get(agent) : undefined;
    if (!s) throw new Failure('not_found', `no agent named ${String(agent)}`);
    return s;
  }
  private record(s: Session, kind: 'text' | 'key', value: string, actor: string) { s.received.push({ kind, value, actor }); }

  private visible(s: Session): string[] {
    const tail = s.lines.slice(-s.rows);
    return [...tail, ...Array(s.rows - tail.length).fill('')];
  }
  private scrolledOff(s: Session): number { return Math.max(0, s.lines.length - s.rows); }

  /** Applies a change to the screen text and sends the changed rows to everyone attached. */
  private edit(s: Session, change: () => void): void {
    const before = this.visible(s);
    change();
    const changed = this.visible(s).flatMap((text, y) => (text !== before[y] ? [{ y, row: row(text) }] : []));
    if (changed.length) this.pushTerminal(s, 'frame', (seq): Notifications['frame'] => ({ terminal: tid(s), seq, full: false, changed, cursor: null }));
  }
  private pushTerminal(s: Session, method: 'frame' | 'resize' | 'marker', build: (seq: number) => object): void {
    const params = build(++s.seq);
    for (const c of this.conns) if (c.attached.has(tid(s))) this.notify(c, method, params);
  }

  private view(s: Session): AgentView {
    return {
      name: s.fx.name, role: s.fx.role, model: s.fx.model, effort: s.fx.effort, state: s.state, state_since: s.since, state_detail: '',
      block: s.fx.wall ? { kind: 'wall', since: s.since } : null,
      wall_reason: s.fx.wall ? 'usage limit' : null,
      account: s.fx.account, task: s.fx.task,
      cache_until: s.open && s.state === 'idle' ? new Date(Date.parse(s.since) + 5 * 60_000).toISOString() : null,
      has_dialog: s.prompt !== null, parent: s.fx.parent,
    };
  }
  private detail(s: Session): AgentDetail {
    return {
      ...this.view(s), worktree: { path: s.fx.worktree, branch: s.fx.branch }, terminal: s.hasTerminal ? tid(s) : null,
      session_id: s.sessionId, session_open: s.open, input_holders: [s.holder],
      account_pick: { account: s.fx.account, reason: 'fixture' }, prompt: s.prompt,
    };
  }

  private setState(s: Session, state: string, actor?: Actor) {
    s.state = state; s.since = this.iso();
    this.emit('agents', 'agent.changed', { agent: s.fx.name, actor, data: this.view(s) as unknown as Record<string, unknown> });
  }
  private control(s: Session, actor: Actor, kind: string, text: string) {
    this.emit('agents', 'agent.control', { agent: s.fx.name, actor,
      data: { id: ++this.timelineId, at: this.iso(), kind, actor, text } });
  }

  private write(s: Session, line: string) { this.edit(s, () => { s.lines.push(line); }); }

  private setComposeLine(s: Session) {
    this.edit(s, () => {
      const line = `> ${s.buffer}`;
      if (s.composing) s.lines[s.lines.length - 1] = line; else s.lines.push(line);
      s.composing = true;
    });
  }

  private submit(s: Session, actor: Actor) {
    const text = s.buffer;
    this.edit(s, () => {
      const line = `> ${text}`;
      if (s.composing) s.lines[s.lines.length - 1] = line; else s.lines.push(line);
      s.composing = false; s.buffer = '';
    });
    if (s.state === 'idle') this.startTurn(s, actor);
  }

  private startTurn(s: Session, actor: Actor) {
    this.setState(s, 'running', actor);
    s.queue = [
      () => this.write(s, '● Thinking…'),
      () => this.write(s, '● I will push the branch.'),
      () => this.openPrompt(s),
    ];
  }

  private openPrompt(s: Session) {
    const lines = PROMPT_LINES.map((l) => l.replace('{branch}', s.fx.branch));
    const prompt: Prompt = {
      id: `p${++s.counter}-${s.fx.name}`, agent: s.fx.name, kind: 'permission', tool: 'Bash',
      command: `git push origin ${s.fx.branch}`, reason: null, lines, since: this.iso(),
      options: [{ key: '1', label: 'Yes', answer: 'yes', highlighted: true }, { key: '2', label: 'No', answer: 'no' }],
    };
    s.prompt = prompt;
    for (const l of lines) this.write(s, l);
    this.setState(s, 'waiting');
    this.emit('notifications', 'prompt_asked', { agent: s.fx.name, title: 'Permission: Bash', data: prompt as unknown as Record<string, unknown> });
  }

  private answer(s: Session, key: string, actor: Actor) {
    s.prompt = null;
    if (key === '1') {
      s.queue = [
        () => { this.setState(s, 'running'); this.write(s, `● Ran git push origin ${s.fx.branch}`); },
        () => this.write(s, '● Pushed. Done.'),
        () => this.setState(s, 'idle'),
      ];
    } else {
      s.queue = [() => { this.write(s, '● Understood, I will not push.'); this.setState(s, 'idle'); }];
    }
    this.setState(s, 'running', actor);
  }

  private interruptTurn(s: Session, actor: Actor) {
    if (s.state === 'idle') return;
    s.prompt = null; s.queue = [];
    this.write(s, '● Interrupted · What should Claude do instead?');
    this.setState(s, 'idle', actor);
  }

  private pressKey(s: Session, key: string, actor: Actor) {
    this.record(s, 'key', key, actor.name);
    if (key === 'esc' || key === 'ctrl-c') this.interruptTurn(s, actor);
    else if (key === 'enter') { if (s.buffer) this.submit(s, actor); }
    else if (s.prompt?.options.some((o) => o.key === key)) this.answer(s, key, actor);
  }

  // ---- events ----

  /** Public so a test can make changes while no client is connected. */
  emit(topic: Topic, type: string, f: { agent?: string; actor?: Actor; title?: string; data?: Record<string, unknown> } = {}) {
    const e: WireEvent = { cursor: ++this.head, topic, type, at: this.iso(), actor: f.actor ?? null, agent: f.agent ?? null,
      title: f.title ?? null, target: f.agent ? { screen: 'session', id: f.agent } : null, data: f.data ?? {} };
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
    try { req = JSON.parse(line); } catch {
      return this.reply(c, null, { error: { code: -32700, message: 'The request is not valid JSON.', data: { code: 'bad_request' } } });
    }
    if (typeof req.method !== 'string' || req.id === undefined) {
      return this.reply(c, req.id ?? null, this.fail(badRequest('A request needs an id and a method.')));
    }
    const params = req.params ?? {};
    let body: Body;
    try { body = { result: this.withReplay(c, req.method, params) }; }
    catch (e) { if (!(e instanceof Failure)) throw e; body = this.fail(e); }
    this.reply(c, req.id, body);
    if (req.method === 'subscribe' && !body.error) this.replay(c, params.since as number | undefined);
  }

  /** A write's successful reply is kept by request_id; the same id with the same params returns it, repeating nothing. */
  private withReplay(c: Conn, method: string, p: Record<string, unknown>): unknown {
    const spec = METHODS[method as MethodName];
    if (!spec?.write || typeof p.request_id !== 'string') return this.dispatch(c, method, p);
    const { request_id, ...rest } = p;
    const sig = JSON.stringify(rest);
    const kept = this.replies.get(request_id);
    if (kept) {
      if (kept.params !== sig) throw badRequest('That request_id was used for different params.', 'request_id');
      return { ...kept.result, replayed: true };
    }
    const result = this.dispatch(c, method, p) as Record<string, unknown>;
    this.replies.set(request_id, { params: sig, result });
    return result;
  }

  // Replayed events follow the subscribe reply, never precede it.
  private replay(c: Conn, since: number | undefined) {
    if (since === undefined) return;
    if (since < this.floor) { this.notify(c, 'gap', { reason: 'retention', cursor: this.head } satisfies Notifications['gap']); return; }
    for (const e of this.events) if (e.cursor > since && c.topics?.has(e.topic)) this.notify(c, 'event', e);
  }

  private accepted(said: string, extra: Record<string, unknown> = {}) { return { accepted: true, said, ...extra }; }
  private actor(c: Conn): Actor { return { kind: c.caller.kind === 'device' ? 'device' : c.caller.kind === 'agent' ? 'agent' : 'user', name: c.caller.name }; }

  private dispatch(c: Conn, method: string, p: Record<string, unknown>): unknown {
    const spec = METHODS[method as MethodName];
    if (method === 'hello') {
      if (typeof p.client !== 'string') throw badRequest('hello needs client', 'client');
      if (typeof p.protocol !== 'number') throw badRequest('hello needs protocol', 'protocol');
      if (typeof p.token === 'string') {
        const device = this.tokens.get(p.token);
        if (!c.network || !device) throw refused('device_token', 'that device token is not valid');
        c.role = 'member'; c.caller = { kind: 'device', name: device };
      }
      c.helloed = true;
      if (c.caller.name === 'unknown') c.caller = { ...c.caller, name: p.client };
      return {
        protocol: PROTOCOL, min_client: 1, daemon: 'fake0000', schema: 1, role: c.role, caller: c.caller,
        capabilities: { chat: false, review: false, input_ask: false, terminal_format: ['text'] }, transport: c.network ? 'network' : 'local',
        methods: Object.entries(METHODS).map(([name, m]) => ({ name, since: m.since, role: m.role, allowed: this.mayCall(c.role, m.role) })),
      };
    }
    if (!c.helloed) throw badRequest('say hello first');
    if (!spec) throw new Failure('not_found', `unknown method ${method}`);
    if (!this.mayCall(c.role, spec.role)) throw refused('role', `the ${c.role} role may not call ${method}`);
    if (spec.write && typeof p.request_id !== 'string') throw badRequest(`${method} needs a request_id`, 'request_id');
    const actor = this.actor(c);
    const agent = () => this.session(p.agent);
    switch (method as MethodName) {
      case 'whoami': return { caller: c.caller, role: c.role, methods: Object.entries(METHODS).filter(([, m]) => this.mayCall(c.role, m.role)).map(([n]) => n) };
      case 'daemon.status': {
        const by_state: Record<string, number> = {};
        for (const s of this.sessions.values()) by_state[s.state] = (by_state[s.state] ?? 0) + 1;
        return { alive: true, version: 'fake0000', protocol: PROTOCOL, schema: 1, started_at: this.iso(),
          terminal_host: { name: 'fake', health: 'ok' }, agents: { total: this.sessions.size, by_state }, inbox: { unread: 0 } };
      }
      case 'agents.list': return { agents: [...this.sessions.values()].map((s) => this.view(s)) };
      case 'agent.get': return this.detail(agent());
      case 'subscribe': {
        if (!Array.isArray(p.topics) || !p.topics.length) throw badRequest('topics must be a non-empty list', 'topics');
        c.topics = new Set(p.topics as Topic[]);
        return { cursor: this.head };
      }
      case 'unsubscribe': {
        for (const t of (p.topics as Topic[] | undefined) ?? []) c.topics?.delete(t);
        return {};
      }
      case 'prompt.list': { const s = agent(); return { prompts: s.prompt ? [s.prompt] : [] }; }
      case 'session.start': return this.startSession(agent(), false, actor);
      case 'session.wake': return this.startSession(agent(), true, actor);
      case 'session.restart': return this.restartSession(agent(), p.fresh === true, actor);
      case 'session.park': return this.stopSession(agent(), 'close', actor);
      case 'session.stop': return this.stopSession(agent(), p.mode, actor);
      case 'session.input': return this.input(agent(), p, c, actor);
      case 'session.keys': return this.keys(agent(), p.keys, c, actor);
      case 'session.interrupt': {
        const s = agent(); this.gate(s, c);
        this.record(s, 'key', 'esc', actor.name);
        this.control(s, actor, 'interrupt', `${actor.name} interrupted`); this.interruptTurn(s, actor);
        return this.accepted('Interrupted.');
      }
      case 'prompt.answer': {
        const s = agent(); this.gate(s, c);
        const open = s.prompt;
        if (!open || open.id !== p.prompt) throw new Failure('stale_prompt', 'That prompt is no longer the open one; nothing was sent.', { current: open });
        if (typeof p.choice !== 'string') throw badRequest('this prompt takes one of its option keys', 'choice');
        const key = p.choice;
        if (!open.options.some((o) => o.key === key)) throw badRequest(`${key} is not offered by this prompt`, 'choice');
        this.record(s, 'key', key, actor.name);
        this.control(s, actor, 'prompt.answer', `${actor.name} answered ${key}`); this.answer(s, key, actor);
        return this.accepted('Answered.');
      }
      case 'input.take': {
        const s = agent();
        if (s.holder.kind !== 'daemon' && s.holder.name !== c.caller.name) throw refused('input_holder', `${s.holder.name} holds the input`);
        if (s.holder.kind === 'daemon') s.holder = { kind: c.caller.kind === 'device' ? 'device' : 'person', name: c.caller.name, since: this.iso() };
        this.control(s, actor, 'input.take', `${actor.name} took the input`);
        return this.accepted('You hold the input.', { holder: s.holder });
      }
      case 'input.release': {
        const s = agent();
        if (s.holder.name !== c.caller.name || s.holder.kind === 'daemon') throw refused('input_holder', 'you do not hold the input');
        s.holder = DAEMON_HOLDER;
        this.control(s, actor, 'input.release', `${actor.name} released the input`);
        return this.accepted('The daemon holds the input again.');
      }
      case 'terminal.attach': {
        const s = p.terminal !== undefined ? this.byTerminal(p.terminal) : agent();
        if (p.terminal !== undefined && p.agent !== undefined) throw badRequest('give agent or terminal, not both', 'terminal');
        if (!s.hasTerminal) throw refused('state', 'the agent has no terminal');
        c.attached.add(tid(s));
        const want = typeof p.history === 'number' ? p.history : 100;
        const off = this.scrolledOff(s), first = Math.max(0, off - want);
        return { terminal: tid(s), format: 'text', seq: s.seq, cols: s.cols, rows: s.rows, lines: this.visible(s).map(row), cursor: null,
          history: { first, lines: s.lines.slice(first, off).map(row), more: first > 0 }, holder: s.holder };
      }
      case 'terminal.detach': c.attached.delete(String(p.terminal)); return {};
      case 'terminal.history': {
        const s = this.byTerminal(p.terminal);
        const end = Math.min(Number(p.before), this.scrolledOff(s)), first = Math.max(0, end - Number(p.limit));
        return { first, lines: s.lines.slice(first, end).map(row), more: first > 0 };
      }
      case 'terminal.resize': {
        const s = this.byTerminal(p.terminal);
        if (s.holder.name !== c.caller.name || s.holder.kind === 'daemon') throw refused('input_holder', 'only the input holder may resize');
        s.cols = Number(p.cols); s.rows = Number(p.rows);
        this.pushTerminal(s, 'resize', (seq): Notifications['resize'] => ({ terminal: tid(s), seq, cols: s.cols, rows: s.rows }));
        return this.accepted('Resized.');
      }
      case 'device.redeem': {
        const code = String(p.code ?? '').trim().toUpperCase();
        if (!c.network || !this.codes.delete(code)) throw refused('pairing_code', 'that pairing code is not valid');
        const token = crypto.randomBytes(16).toString('hex'), device = String(p.name);
        this.tokens.set(token, device);
        return this.accepted('Paired.', { token, device });
      }
      default: throw refused('unsupported', `the fake daemon does not serve ${method}`);
    }
  }

  private byTerminal(id: unknown): Session {
    const s = [...this.sessions.values()].find((x) => tid(x) === id);
    if (!s) throw new Failure('not_found', `no terminal ${String(id)}`);
    return s;
  }

  private mayCall(role: Role, lowest: Role): boolean { return ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(lowest); }

  /** The gates every typing method passes: a session to type at, and nobody else holding the input. */
  private gate(s: Session, c: Conn) {
    if (!s.open) throw refused('state', 'the agent has no open session');
    if (s.holder.kind !== 'daemon' && s.holder.name !== c.caller.name) throw refused('input_holder', `${s.holder.name} holds the input`);
  }

  /** Opens a session; the new screen follows the old text, as a terminal's scrollback would. */
  private startSession(s: Session, wake: boolean, actor: Actor) {
    if (s.open) throw refused('state', 'the session is already open');
    if (wake && !s.sessionId) throw refused('state', 'there is no closed session to resume');
    s.open = true; s.hasTerminal = true;
    s.sessionId ??= `s-${s.fx.name}-${++s.counter}`;
    this.edit(s, () => { s.lines.push(...BANNER.map((l) => l.replace('{worktree}', s.fx.worktree))); });
    this.setState(s, 'idle', actor);
    return this.accepted('Session started.');
  }

  private restartSession(s: Session, fresh: boolean, actor: Actor) {
    if (s.open) this.stopSession(s, 'close', actor);
    if (fresh) s.sessionId = null;
    return this.startSession(s, false, actor);
  }

  private stopSession(s: Session, mode: unknown, actor: Actor) {
    if (mode !== 'exit' && mode !== 'close' && mode !== 'kill') throw badRequest('mode must be exit, close or kill', 'mode');
    if (!s.open) throw refused('state', 'the agent has no open session');
    s.prompt = null; s.queue = []; s.open = false;
    if (mode === 'kill') s.hasTerminal = false; else this.write(s, '(session ended)');
    this.setState(s, mode === 'kill' ? 'no_terminal' : 'parked', actor);
    return this.accepted('Session ended.');
  }

  private input(s: Session, p: Record<string, unknown>, c: Conn, actor: Actor) {
    if (typeof p.text !== 'string') throw badRequest('input needs text', 'text');
    this.gate(s, c);
    if (s.fx.wall) throw refused('wall', 'the account is at its usage limit; typing would only spend quota');
    if (s.prompt) throw refused('screen_blocker', 'a dialog is open, so a typed line would answer it', 'use keys or prompt.answer');
    this.record(s, 'text', p.text, actor.name);
    this.control(s, actor, 'input', `${actor.name} typed`);
    s.buffer += p.text;
    if (p.enter !== false) this.submit(s, actor); else this.setComposeLine(s);
    return this.accepted('Typed.');
  }

  private keys(s: Session, keys: unknown, c: Conn, actor: Actor) {
    if (!Array.isArray(keys) || !keys.length || !keys.every((k) => KEYS.has(k))) throw badRequest('keys must be named keys', 'keys');
    this.gate(s, c);
    this.control(s, actor, 'keys', `${actor.name} pressed ${keys.join(' ')}`);
    for (const k of keys as string[]) this.pressKey(s, k, actor);
    return this.accepted('Keys sent.');
  }
}

const tid = (s: Session) => `term:${s.fx.name}`;
