// A fake daemon that answers the JSON-RPC contract of docs/daemon-plan.md from fixtures, with one
// scripted fake session per agent. The UI cannot tell it from the real socket; the real daemon is a swap.
import {
  BARRED_FOR_AGENT, PROTOCOL, WRITE_METHODS,
  type AgentDetail, type AgentView, type EventNotice, type ErrorCode, type FrameNotice, type HelloParams,
  type InputHolder, type MethodName, type NamedKey, type Prompt, type Role, type RpcErrorBody, type Topic,
} from '../src/protocol.ts';
import type { Transport } from '../src/transport.ts';
import { AGENTS, BANNER, PROMPT_LINES, type AgentFixture } from './fixtures.ts';

const ROWS = 24, COLS = 80, HISTORY_PAGE = 50;
const NAMED_KEYS = new Set(['esc', 'enter', 'up', 'down', 'tab', 'shift-tab', 'ctrl-c', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']);

// Every method and the protocol it appeared in.
const METHODS: Record<MethodName, number> = {
  'hello': 1, 'whoami': 1, 'daemon.status': 1, 'agents.list': 1, 'agent.get': 1, 'subscribe': 1, 'unsubscribe': 1,
  'session.start': 1, 'session.wake': 1, 'session.stop': 1, 'session.input': 1, 'session.keys': 1,
  'session.interrupt': 1, 'prompt.list': 1, 'prompt.answer': 1, 'input.take': 1, 'input.release': 1,
  'terminal.attach': 1, 'terminal.detach': 1, 'terminal.history': 1, 'terminal.resize': 1,
};

class Refusal extends Error {
  code: ErrorCode;
  reason?: string;
  constructor(code: ErrorCode, message: string, reason?: string) { super(message); this.code = code; this.reason = reason; }
}
const refused = (why: string) => new Refusal('refused', why, why);

interface Session {
  fx: AgentFixture;
  state: AgentView['state'];
  since: string;
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
  promptCount: number;
  received: { kind: 'text' | 'key'; value: string; actor: string }[]; // what reached the terminal
}

interface Conn {
  transport: Transport;
  role: Role;
  client: string;
  helloed: boolean;
  topics: Set<Topic> | null;
  attached: Set<string>; // terminal ids
}

export interface FakeDaemonOptions {
  retention?: number; // events kept for resume; older ones give `gap`
  now?: () => Date;
}

export class FakeDaemon {
  readonly sessions = new Map<string, Session>();
  private events: EventNotice[] = [];
  private head = 0;
  private floor = 0; // cursor of the newest event dropped by retention
  private conns = new Set<Conn>();
  private replies = new Map<string, { result?: unknown; error?: RpcErrorBody }>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private retention: number;
  private now: () => Date;

  constructor(opts: FakeDaemonOptions = {}) {
    this.retention = opts.retention ?? 1000;
    this.now = opts.now ?? (() => new Date());
    for (const fx of AGENTS) this.sessions.set(fx.name, this.newSession(fx));
  }

  private iso() { return this.now().toISOString(); }

  private newSession(fx: AgentFixture): Session {
    const open = fx.session !== null;
    const s: Session = {
      fx, state: fx.state, since: this.iso(), sessionId: fx.session, open, hasTerminal: fx.state !== 'no_terminal',
      lines: open ? BANNER.map((l) => l.replace('{worktree}', fx.worktree)) : [], seq: 0, cols: COLS, rows: ROWS,
      buffer: '', composing: false, holder: { who: 'daemon', since: this.iso() }, prompt: null,
      queue: [], promptCount: 0, received: [],
    };
    return s;
  }

  // ---- hosting ----

  /** Serves one connection. Returns a handle the test can use to cut it. */
  accept(transport: Transport, role: Role = 'founder'): void {
    const conn: Conn = { transport, role, client: 'unknown', helloed: false, topics: null, attached: new Set() };
    this.conns.add(conn);
    transport.onmessage = (m) => this.onMessage(conn, m);
    transport.onclose = () => { this.conns.delete(conn); };
  }

  dropConnections(): void { for (const c of [...this.conns]) c.transport.close(); }
  connectionCount(): number { return this.conns.size; }

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
    if (s.holder.who !== 'daemon') throw refused(`${s.holder.who} holds the input`);
    this.record(s, 'text', text, 'daemon');
    this.edit(s, () => { s.lines.push(`> ${text}`); }, { kind: 'daemon_typed', text });
  }

  private session(agent: string): Session {
    const s = this.sessions.get(agent);
    if (!s) throw new Refusal('not_found', `no agent named ${agent}`);
    return s;
  }

  private record(s: Session, kind: 'text' | 'key', value: string, actor: string) { s.received.push({ kind, value, actor }); }

  private visible(s: Session): string[] {
    const tail = s.lines.slice(-s.rows);
    return [...tail, ...Array(s.rows - tail.length).fill('')];
  }
  private scrolledOff(s: Session): number { return Math.max(0, s.lines.length - s.rows); }

  /** Applies a change to the screen text and sends the changed rows to everyone attached. */
  private edit(s: Session, change: () => void, marker?: { kind: 'daemon_typed' | 'control'; text: string }): void {
    const before = this.visible(s), offBefore = this.scrolledOff(s);
    change();
    const after = this.visible(s), scrolled = this.scrolledOff(s) - offBefore;
    const shifted = [...before.slice(scrolled), ...Array(scrolled).fill('')];
    const rows = after.flatMap((text, row) => (text !== shifted[row] ? [{ row, text }] : []));
    if (rows.length || scrolled) {
      this.pushTerminal(s, 'frame', (seq): FrameNotice => ({ terminal: tid(s), seq, scrolled, rows }));
    }
    if (marker) {
      const row = Math.min(s.lines.length, s.rows) - 1;
      this.pushTerminal(s, 'marker', (seq) => ({ terminal: tid(s), seq, row, ...marker }));
    }
  }
  private pushTerminal(s: Session, method: string, build: (seq: number) => object): void {
    const seq = ++s.seq;
    const params = build(seq);
    for (const c of this.conns) if (c.attached.has(tid(s))) this.notify(c, method, params);
  }

  private setState(s: Session, state: AgentView['state'], kind = 'state', actor?: string) {
    s.state = state; s.since = this.iso();
    this.emit('agents', kind, s.fx.name, actor);
  }

  private write(s: Session, line: string) { this.edit(s, () => { s.lines.push(line); }); }

  private setComposeLine(s: Session) {
    this.edit(s, () => {
      const line = `> ${s.buffer}`;
      if (s.composing) s.lines[s.lines.length - 1] = line; else s.lines.push(line);
      s.composing = true;
    });
  }

  private submit(s: Session, actor: string) {
    const text = s.buffer;
    this.edit(s, () => {
      const line = `> ${text}`;
      if (s.composing) s.lines[s.lines.length - 1] = line; else s.lines.push(line);
      s.composing = false; s.buffer = '';
    });
    if (s.state === 'idle') this.startTurn(s, actor);
  }

  private startTurn(s: Session, actor: string) {
    this.setState(s, 'running', 'state', actor);
    s.queue = [
      () => this.write(s, '● Thinking…'),
      () => this.write(s, '● I will push the branch.'),
      () => this.openPrompt(s),
    ];
  }

  private openPrompt(s: Session) {
    const id = `p${++s.promptCount}-${s.fx.name}`;
    const lines = PROMPT_LINES.map((l) => l.replace('{branch}', s.fx.branch));
    s.prompt = {
      id, kind: 'permission', agent: s.fx.name, tool: 'Bash', command: `git push origin ${s.fx.branch}`,
      reason: null, lines, options: [{ key: '1', label: 'Yes' }, { key: '2', label: 'No' }],
    };
    for (const l of lines) this.write(s, l);
    this.setState(s, 'waiting');
    this.emit('requests', 'prompt_open', s.fx.name, undefined, { prompt: id });
  }

  private closePrompt(s: Session, how: string, actor: string) {
    const id = s.prompt!.id;
    s.prompt = null;
    this.emit('requests', 'prompt_closed', s.fx.name, actor, { prompt: id, how });
  }

  private answer(s: Session, key: NamedKey, actor: string) {
    this.closePrompt(s, key === '1' ? 'yes' : 'no', actor);
    if (key === '1') {
      s.queue = [
        () => { this.setState(s, 'running'); this.write(s, `● Ran git push origin ${s.fx.branch}`); },
        () => this.write(s, '● Pushed. Done.'),
        () => this.setState(s, 'idle'),
      ];
    } else {
      s.queue = [() => { this.write(s, '● Understood, I will not push.'); this.setState(s, 'idle'); }];
    }
    this.setState(s, 'running', 'state', actor);
  }

  private interruptTurn(s: Session, actor: string) {
    if (s.state === 'idle') return;
    if (s.prompt) this.closePrompt(s, 'interrupted', actor);
    s.queue = [];
    this.write(s, '● Interrupted · What should Claude do instead?');
    this.setState(s, 'idle', 'state', actor);
  }

  private pressKey(s: Session, key: NamedKey, actor: string) {
    this.record(s, 'key', key, actor);
    if (key === 'esc' || key === 'ctrl-c') this.interruptTurn(s, actor);
    else if (key === 'enter') { if (s.buffer) this.submit(s, actor); }
    else if (s.prompt && s.prompt.options.some((o) => o.key === key)) this.answer(s, key, actor);
  }

  // ---- events ----

  /** Public so a test can make changes while no client is connected. */
  emit(topic: Topic, kind: string, agent?: string, actor?: string, data?: Record<string, unknown>) {
    const e: EventNotice = { cursor: ++this.head, topic, kind, agent, actor, data };
    this.events.push(e);
    while (this.events.length > this.retention) this.floor = this.events.shift()!.cursor;
    for (const c of this.conns) if (c.topics?.has(topic)) this.notify(c, 'event', e);
  }

  // ---- wire ----

  private notify(c: Conn, method: string, params: unknown) {
    c.transport.send(JSON.stringify({ jsonrpc: '2.0', method, params }));
  }
  private reply(c: Conn, id: unknown, body: { result?: unknown; error?: RpcErrorBody }) {
    c.transport.send(JSON.stringify({ jsonrpc: '2.0', id, ...body }));
  }

  private onMessage(c: Conn, line: string) {
    let req: { id?: number | string; method?: string; params?: Record<string, unknown> };
    try { req = JSON.parse(line); } catch {
      return this.reply(c, null, { error: { code: -32700, message: 'The request is not valid JSON.', data: { code: 'bad_request' } } });
    }
    if (typeof req.method !== 'string' || req.id === undefined) {
      return this.reply(c, req.id ?? null, { error: { code: -32600, message: 'A request needs an id and a method.', data: { code: 'bad_request' } } });
    }
    const params = req.params ?? {};
    let body: { result?: unknown; error?: RpcErrorBody };
    const rid = typeof params.request_id === 'string' ? params.request_id : null;
    const isWrite = WRITE_METHODS.has(req.method);
    if (isWrite && rid && this.replies.has(rid)) body = this.replies.get(rid)!; // a retry: no second action
    else {
      try {
        const result = this.dispatch(c, req.method, params);
        body = { result };
      } catch (e) {
        if (!(e instanceof Refusal)) throw e;
        body = { error: { code: e.code === 'not_found' ? -32601 : -32000, message: e.message, data: { code: e.code, reason: e.reason } } };
      }
      if (isWrite && rid) this.replies.set(rid, body);
    }
    this.reply(c, req.id, body);
    this.afterReply(c, req.method, params, body);
  }

  // Replayed events follow the subscribe reply, never precede it.
  private afterReply(c: Conn, method: string, params: Record<string, unknown>, body: { result?: unknown; error?: RpcErrorBody }) {
    if (method !== 'subscribe' || body.error) return;
    const since = params.since as number | undefined;
    if (since === undefined) return;
    if (since < this.floor) { this.notify(c, 'gap', { cursor: this.head }); return; }
    for (const e of this.events) if (e.cursor > since && c.topics?.has(e.topic)) this.notify(c, 'event', e);
  }

  private bad(why: string): never { throw new Refusal('bad_request', why); }

  private dispatch(c: Conn, method: string, p: Record<string, unknown>): unknown {
    if (method === 'hello') {
      const h = p as unknown as HelloParams;
      if (typeof h.client !== 'string' || typeof h.protocol !== 'number') this.bad('hello needs client and protocol');
      c.client = h.client; c.helloed = true;
      const methods = Object.fromEntries(Object.entries(METHODS).filter(([m]) => this.mayCall(c.role, m)));
      return { protocol: PROTOCOL, min_client: 1, daemon: 'fake0000', schema: 1, role: c.role, methods };
    }
    if (!c.helloed) this.bad('say hello first');
    if (!(method in METHODS)) throw new Refusal('not_found', `unknown method ${method}`);
    if (!this.mayCall(c.role, method)) throw refused(`the ${c.role} role may not call ${method}`);
    if (WRITE_METHODS.has(method) && typeof p.request_id !== 'string') this.bad(`${method} needs a request_id`);
    const actor = c.client;
    const agent = () => { if (typeof p.agent !== 'string') this.bad('agent is required'); return this.session(p.agent); };
    switch (method) {
      case 'whoami': return { viewer: actor, role: c.role, methods: Object.keys(METHODS).filter((m) => this.mayCall(c.role, m)) };
      case 'daemon.status': {
        const counts: Record<string, number> = {};
        for (const s of this.sessions.values()) counts[s.state] = (counts[s.state] ?? 0) + 1;
        return { alive: true, version: 'fake0000', protocol: PROTOCOL, host: { name: 'fake', healthy: true }, counts, inbox: 0 };
      }
      case 'agents.list': return { agents: [...this.sessions.values()].map((s) => this.view(s)) };
      case 'agent.get': return this.detail(agent());
      case 'subscribe': {
        const topics = p.topics;
        if (!Array.isArray(topics) || !topics.length) this.bad('topics must be a non-empty list');
        c.topics = new Set(topics as Topic[]);
        return { cursor: this.head };
      }
      case 'unsubscribe': c.topics = null; return {};
      case 'prompt.list': { const s = agent(); return { prompts: s.prompt ? [s.prompt] : [] }; }
      case 'session.start': case 'session.wake': return this.startSession(agent(), method === 'session.wake', actor);
      case 'session.stop': return this.stopSession(agent(), p.mode as string, actor);
      case 'session.input': return this.input(agent(), p, actor);
      case 'session.keys': return this.keys(agent(), p.keys, actor);
      case 'session.interrupt': {
        const s = agent(); this.gate(s, actor);
        this.record(s, 'key', 'esc', actor);
        this.control(s, actor, 'interrupt'); this.interruptTurn(s, actor);
        return { accepted: true };
      }
      case 'prompt.answer': {
        const s = [...this.sessions.values()].find((x) => x.prompt?.id === p.prompt)
          ?? [...this.sessions.values()].find((x) => typeof p.prompt === 'string' && p.prompt.endsWith(`-${x.fx.name}`));
        if (!s) throw new Refusal('not_found', `no prompt ${String(p.prompt)}`);
        this.gate(s, actor);
        if (s.prompt?.id !== p.prompt) throw new Refusal('stale_prompt', 'That prompt is no longer the open one; nothing was sent.', 'stale_prompt');
        const key = p.choice as NamedKey;
        if (!s.prompt.options.some((o) => o.key === key)) this.bad(`${String(key)} is not offered by this prompt`);
        this.record(s, 'key', key, actor);
        this.control(s, actor, 'prompt.answer'); this.answer(s, key, actor);
        return { accepted: true };
      }
      case 'input.take': {
        const s = agent();
        if (s.holder.who !== 'daemon' && s.holder.who !== actor) throw refused(`${s.holder.who} holds the input`);
        if (s.holder.who === 'daemon') s.holder = { who: actor, since: this.iso() };
        this.control(s, actor, 'input.take'); return { holders: [s.holder] };
      }
      case 'input.release': {
        const s = agent();
        if (s.holder.who !== actor) throw refused('you do not hold the input');
        s.holder = { who: 'daemon', since: this.iso() };
        this.control(s, actor, 'input.release'); return { holders: [s.holder] };
      }
      case 'terminal.attach': {
        const s = agent();
        if (!s.hasTerminal) throw refused('the agent has no terminal');
        c.attached.add(tid(s));
        const hist = s.lines.slice(0, this.scrolledOff(s));
        return { terminal: tid(s), seq: s.seq, cols: s.cols, rows: s.rows, frame: this.visible(s),
          history: hist.slice(-HISTORY_PAGE), history_more: hist.length > HISTORY_PAGE };
      }
      case 'terminal.detach': c.attached.delete(String(p.terminal)); return {};
      case 'terminal.history': {
        const s = this.byTerminal(String(p.terminal));
        const before = Number(p.before), limit = Number(p.limit);
        const hist = s.lines.slice(0, this.scrolledOff(s));
        const end = Math.max(0, hist.length - before), start = Math.max(0, end - limit);
        return { lines: hist.slice(start, end), more: start > 0 };
      }
      case 'terminal.resize': {
        const s = this.byTerminal(String(p.terminal));
        if (s.holder.who !== actor) throw refused('only the input holder may resize');
        s.cols = Number(p.cols); s.rows = Number(p.rows);
        this.pushTerminal(s, 'resize', (seq) => ({ terminal: tid(s), seq, cols: s.cols, rows: s.rows }));
        return { accepted: true };
      }
    }
    throw new Refusal('internal', `${method} is declared but not served`);
  }

  private byTerminal(id: string): Session {
    const s = [...this.sessions.values()].find((x) => tid(x) === id);
    if (!s) throw new Refusal('not_found', `no terminal ${id}`);
    return s;
  }

  private mayCall(role: Role, method: string): boolean { return !(role === 'agent' && BARRED_FOR_AGENT.has(method)); }

  /** The gates every typing method passes: a session to type at, and nobody else holding the input. */
  private gate(s: Session, actor: string) {
    if (!s.open) throw refused('the agent has no open session');
    if (s.holder.who !== 'daemon' && s.holder.who !== actor) throw refused(`${s.holder.who} holds the input`);
  }
  private control(s: Session, actor: string, what: string) {
    this.emit('agents', 'control', s.fx.name, actor, { method: what });
  }

  private startSession(s: Session, wake: boolean, actor: string) {
    if (s.open) throw refused('the session is already open');
    if (wake && !s.sessionId) throw refused('there is no closed session to resume');
    s.open = true; s.hasTerminal = true;
    s.sessionId ??= `s-${s.fx.name}-${++s.promptCount}`;
    s.lines = BANNER.map((l) => l.replace('{worktree}', s.fx.worktree));
    this.edit(s, () => {});
    this.pushTerminal(s, 'frame', (seq): FrameNotice => ({ terminal: tid(s), seq, scrolled: 0, rows: this.visible(s).flatMap((text, row) => (text ? [{ row, text }] : [])) }));
    this.setState(s, 'idle', 'state', actor);
    return { accepted: true };
  }

  private stopSession(s: Session, mode: string, actor: string) {
    if (!['exit', 'close', 'kill'].includes(mode)) this.bad('mode must be exit, close or kill');
    if (!s.open) throw refused('the agent has no open session');
    if (s.holder.who !== 'daemon' && s.holder.who !== actor) throw refused(`${s.holder.who} holds the input`);
    if (s.prompt) this.closePrompt(s, 'stopped', actor);
    s.queue = []; s.open = false;
    if (mode === 'kill') s.hasTerminal = false; else this.write(s, '(session ended)');
    this.setState(s, mode === 'kill' ? 'no_terminal' : 'parked', 'state', actor);
    return { accepted: true };
  }

  private input(s: Session, p: Record<string, unknown>, actor: string) {
    if (typeof p.text !== 'string' || typeof p.enter !== 'boolean') this.bad('input needs text and enter');
    this.gate(s, actor);
    if (s.fx.wall) throw refused('the account is at its usage limit; typing would only spend quota');
    if (s.prompt) throw refused('a dialog is open, so a typed line would answer it; use keys or prompt.answer');
    this.record(s, 'text', p.text, actor);
    this.control(s, actor, 'input');
    s.buffer += p.text;
    if (p.enter) this.submit(s, actor); else this.setComposeLine(s);
    return { accepted: true };
  }

  private keys(s: Session, keys: unknown, actor: string) {
    if (!Array.isArray(keys) || !keys.length || !keys.every((k) => NAMED_KEYS.has(k))) this.bad('keys must be named keys');
    this.gate(s, actor);
    if (s.fx.wall) throw refused('the account is at its usage limit');
    this.control(s, actor, 'keys');
    for (const k of keys as NamedKey[]) this.pressKey(s, k, actor);
    return { accepted: true };
  }

  private view(s: Session): AgentView {
    return { name: s.fx.name, role: s.fx.role, model: s.fx.model, effort: s.fx.effort, state: s.state, since: s.since,
      account: s.fx.account, task: s.fx.task, has_dialog: s.prompt !== null, parent: s.fx.parent };
  }
  private detail(s: Session): AgentDetail {
    return { ...this.view(s), worktree: s.fx.worktree, branch: s.fx.branch, terminal: tid(s), session: s.sessionId,
      session_open: s.open, input: [s.holder], prompt: s.prompt };
  }
}

const tid = (s: Session) => `term:${s.fx.name}`;
