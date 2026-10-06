import {
  METHODS, PROTOCOL,
  type ErrorCode, type Event as WireEvent, type InputHolder, type MethodName, type Methods,
  type Notifications, type Prompt, type RequestId, type TerminalSnapshot, type Topic,
} from './protocol.ts';
import type { Transport } from './transport.ts';

export class RpcError extends Error {
  // `transport` and `protocol` are the client's own: no connection, and a daemon it must not talk to.
  code: ErrorCode | 'transport' | 'protocol';
  data: Record<string, unknown> | undefined;
  constructor(code: ErrorCode | 'transport' | 'protocol', message: string, data?: Record<string, unknown>) {
    super(message);
    this.code = code; this.data = data;
  }
  /** The gate's own sentence, the command that fixes it, and which gate: from a `refused` error. */
  get reason(): string | undefined { return this.data?.reason as string | undefined; }
  get gate(): string | undefined { return this.data?.gate as string | undefined; }
  get hint(): string | undefined { return this.data?.hint as string | undefined; }
}

/** A call's params; the client fills in request_id on writes. */
export type CallParams<M extends MethodName> = Omit<Methods[M]['params'], 'request_id'> & { request_id?: RequestId };

export type HelloResult = Methods['hello']['result'];

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface ClientOptions {
  connect: () => Promise<Transport>;
  client: string; // who is calling; the daemon names this actor on what it does
  token?: string; // the device token from pairing; the network listener admits no one without it
  reconnect?: { minMs: number; maxMs: number } | false;
  newRequestId?: () => string;
}

type Handler<T> = (value: T) => void;
interface Pending {
  id: number; method: string; params: unknown;
  resolve: (v: unknown) => void; reject: (e: Error) => void;
}

/** What a terminal pane renders: the screen rows, the scrollback fetched so far, and who holds the input. */
export class TerminalView {
  terminal = ''; seq = 0; cols = 0; rows = 0;
  frame: string[] = [];
  history: string[] = []; historyFirst = 0; historyMore = false; // historyFirst: absolute index of history[0]
  holder: InputHolder | null = null;
  markers: Notifications['marker'][] = [];
  agent: string;
  private listeners = new Set<() => void>();
  constructor(agent: string) { this.agent = agent; }

  subscribe(fn: () => void): () => void { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  /** Scrollback then screen, without trailing blank rows. */
  text(): string[] {
    const all = [...this.history, ...this.frame];
    while (all.length && all[all.length - 1] === '') all.pop();
    return all;
  }
  /** True when the notice is the next one; false means a notice was missed and the view must reattach. */
  fits(seq: number): boolean { return seq === this.seq + 1; }

  setSnapshot(s: TerminalSnapshot): void {
    this.terminal = s.terminal; this.seq = s.seq; this.cols = s.cols; this.rows = s.rows;
    this.frame = s.lines.map((r) => r.text);
    this.history = s.history.lines.map((r) => r.text); this.historyFirst = s.history.first; this.historyMore = s.history.more;
    this.holder = s.holder;
    this.emit();
  }
  applyFrame(n: Notifications['frame']): void {
    if (n.full) this.frame = (n.lines ?? []).map((r) => r.text);
    for (const c of n.changed ?? []) this.frame[c.y] = c.row.text;
    this.seq = n.seq; this.emit();
  }
  applyResize(n: Notifications['resize']): void {
    this.cols = n.cols; this.rows = n.rows; this.seq = n.seq;
    this.frame = Array.from({ length: n.rows }, (_, i) => this.frame[i] ?? '');
    this.emit();
  }
  applyMarker(n: Notifications['marker']): void { this.markers.push(n); this.seq = n.seq; this.emit(); }
  prependHistory(first: number, lines: string[], more: boolean): void {
    this.history = [...lines, ...this.history]; this.historyFirst = first; this.historyMore = more; this.emit();
  }
  private emit(): void { for (const fn of this.listeners) fn(); }
}

export class DaemonClient {
  state: ConnectionState = 'connecting';
  hello: HelloResult | null = null;

  private transport: Transport | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private waitingForOpen: { ok: () => void; fail: (e: Error) => void }[] = [];
  private topics = new Set<Topic>();
  private cursor: number | undefined;
  private eventHandlers = new Set<Handler<WireEvent>>();
  private gapHandlers = new Set<Handler<Notifications['gap']>>();
  private stateHandlers = new Set<Handler<ConnectionState>>();
  private views = new Map<string, TerminalView>();
  private holders = new Map<string, number>(); // how many screens hold each agent's view
  private requestCounter = 0;
  private closing = false;

  private opts: ClientOptions;
  constructor(opts: ClientOptions) { this.opts = opts; }

  // ---- connection ----

  /** Connects and says hello. Rejects when the daemon speaks a protocol this client does not know. */
  async start(): Promise<HelloResult> {
    await this.open();
    return this.hello!;
  }

  close(): void {
    this.closing = true;
    this.setState('closed');
    this.transport?.close();
    this.failPending(new RpcError('transport', 'client closed'));
  }

  onState(fn: Handler<ConnectionState>): () => void { this.stateHandlers.add(fn); return () => this.stateHandlers.delete(fn); }

  private setState(s: ConnectionState): void {
    if (this.state === s) return;
    this.state = s;
    for (const fn of this.stateHandlers) fn(s);
  }

  private async open(): Promise<void> {
    const t = await this.opts.connect();
    this.transport = t;
    t.onmessage = (m) => this.receive(t, m);
    t.onclose = () => this.lost(t);
    const hello = await this.callNow('hello', { client: this.opts.client, protocol: PROTOCOL, token: this.opts.token });
    if (hello.min_client > PROTOCOL || hello.protocol < PROTOCOL) {
      t.onclose = null; t.close();
      this.setState('closed'); this.closing = true;
      throw new RpcError('protocol',
        `The daemon speaks protocol ${hello.protocol} (needs client ${hello.min_client}+); this client speaks ${PROTOCOL}.`,
        { reason: 'run `hl service install` to reload the service' });
    }
    this.hello = hello;
    // Pending writes go first with their old ids and request_ids: a daemon that already did one
    // answers from its stored reply instead of repeating it.
    for (const p of this.pending.values()) this.transmit(p);
    await this.establish();
    this.setState('open');
    const waiters = this.waitingForOpen; this.waitingForOpen = [];
    for (const w of waiters) w.ok();
  }

  private async establish(): Promise<void> {
    if (this.topics.size) await this.callNow('subscribe', { topics: [...this.topics], since: this.cursor });
    for (const v of this.views.values()) await this.attach(v);
  }

  private lost(t: Transport): void {
    if (t !== this.transport || this.closing) return;
    this.transport = null;
    this.failInflightInternal();
    const r = this.opts.reconnect;
    if (r === false) { this.setState('closed'); this.failPending(new RpcError('transport', 'connection lost')); return; }
    this.setState('reconnecting');
    void this.reconnect(r ?? { minMs: 250, maxMs: 5000 });
  }

  private async reconnect(r: { minMs: number; maxMs: number }): Promise<void> {
    let delay = r.minMs;
    while (!this.closing) {
      try { await this.open(); return; }
      catch (e) {
        if (e instanceof RpcError && e.code === 'protocol') { this.failPending(e); return; }
        await new Promise((res) => setTimeout(res, delay));
        delay = Math.min(delay * 2, r.maxMs);
      }
    }
  }

  // ---- calls ----

  /** Calls a method. Writes get a request_id here unless the caller gave one. */
  async call<M extends MethodName>(method: M, params: CallParams<M>): Promise<Methods[M]['result']> {
    if (this.closing) throw new RpcError('transport', 'client closed');
    if (this.state !== 'open') await new Promise<void>((ok, fail) => this.waitingForOpen.push({ ok, fail }));
    let p: Record<string, unknown> = params as Record<string, unknown>;
    if (METHODS[method].write && p.request_id === undefined) {
      p = { ...p, request_id: this.opts.newRequestId?.() ?? `${this.opts.client}-${Date.now().toString(36)}-${++this.requestCounter}` };
    }
    return new Promise((resolve, reject) => {
      const entry: Pending = { id: this.nextId++, method, params: p, resolve: resolve as Pending['resolve'], reject };
      this.pending.set(entry.id, entry);
      this.transmit(entry);
    });
  }

  private callNow<M extends MethodName>(method: M, params: CallParams<M>): Promise<Methods[M]['result']> {
    return new Promise((resolve, reject) => {
      const entry: Pending = { id: this.nextId++, method, params, resolve: resolve as Pending['resolve'], reject };
      this.inflightInternal.add(entry.id);
      this.pending.set(entry.id, entry);
      this.transmit(entry);
    });
  }
  // Handshake and re-establish calls belong to one connection: they fail with it instead of being resent.
  private inflightInternal = new Set<number>();
  private failInflightInternal(): void {
    for (const id of this.inflightInternal) {
      this.pending.get(id)?.reject(new RpcError('transport', 'connection lost'));
      this.pending.delete(id);
    }
    this.inflightInternal.clear();
  }

  private transmit(p: Pending): void {
    this.transport?.send(JSON.stringify({ jsonrpc: '2.0', id: p.id, method: p.method, params: p.params }));
  }

  private failPending(e: Error): void {
    const waiters = this.waitingForOpen; this.waitingForOpen = [];
    for (const w of waiters) w.fail(e);
    for (const p of this.pending.values()) p.reject(e);
    this.pending.clear(); this.inflightInternal.clear();
  }

  private receive(t: Transport, line: string): void {
    if (t !== this.transport) return;
    let msg: { id?: unknown; result?: unknown; error?: { message: string; data?: Record<string, unknown> }; method?: string; params?: unknown };
    try { msg = JSON.parse(line); } catch { return; } // a line that is not JSON is not ours to act on
    if (msg.method && msg.id === undefined) { this.notice(msg.method, msg.params); return; }
    if (typeof msg.id !== 'number') return;
    const p = this.pending.get(msg.id);
    if (!p) return; // a reply to a resent call that already finished
    this.pending.delete(msg.id); this.inflightInternal.delete(msg.id);
    if (msg.error) {
      const e = msg.error;
      p.reject(new RpcError((e.data?.code as ErrorCode | undefined) ?? 'internal', e.message, e.data));
    } else p.resolve(msg.result);
  }

  // ---- event stream ----

  /** Streams events for these topics, replacing none already asked for. A reconnect resumes at the last cursor. */
  async subscribe(topics: Topic[]): Promise<void> {
    const before = this.topics.size;
    for (const t of topics) this.topics.add(t);
    if (this.topics.size === before && this.cursor !== undefined) return;
    const r = await this.call('subscribe', { topics: [...this.topics], since: this.cursor });
    this.cursor ??= r.cursor;
  }
  onEvent(fn: Handler<WireEvent>): () => void { this.eventHandlers.add(fn); return () => this.eventHandlers.delete(fn); }
  /** The daemon dropped history this client needed: refetch what the events described. */
  onGap(fn: Handler<Notifications['gap']>): () => void { this.gapHandlers.add(fn); return () => this.gapHandlers.delete(fn); }

  private notice(method: string, params: unknown): void {
    switch (method) {
      case 'event': {
        const e = params as WireEvent;
        if (this.cursor !== undefined && e.cursor <= this.cursor) return; // replayed twice
        this.cursor = e.cursor;
        for (const fn of this.eventHandlers) fn(e);
        break;
      }
      case 'gap': {
        const g = params as Notifications['gap'];
        this.cursor = g.cursor;
        for (const fn of this.gapHandlers) fn(g);
        break;
      }
      case 'frame': case 'resize': case 'marker': {
        const n = params as { terminal: string; seq: number };
        const v = [...this.views.values()].find((x) => x.terminal === n.terminal);
        if (!v) return;
        if (!v.fits(n.seq)) { void this.attach(v).catch(() => {}); return; } // missed one: take a fresh frame
        if (method === 'frame') v.applyFrame(params as Notifications['frame']);
        else if (method === 'resize') v.applyResize(params as Notifications['resize']);
        else v.applyMarker(params as Notifications['marker']);
        break;
      }
    }
  }

  // ---- terminal channel ----

  /** Attaches a view that stays current, through reconnects, until every holder has detached. Holders share one view per agent. */
  async attachTerminal(agent: string): Promise<TerminalView> {
    const v = this.views.get(agent) ?? new TerminalView(agent);
    this.views.set(agent, v);
    this.holders.set(agent, (this.holders.get(agent) ?? 0) + 1);
    try { await this.attach(v); } catch (e) { this.release(agent); throw e; }
    return v;
  }
  /** Lets go of one hold; the daemon is told to stop only when the last holder does. */
  async detachTerminal(agent: string): Promise<void> {
    const v = this.views.get(agent);
    if (!v || !this.release(agent)) return;
    await this.call('terminal.detach', { terminal: v.terminal });
  }
  /** Drops one hold; true when it was the last, and the view is gone. */
  private release(agent: string): boolean {
    const n = (this.holders.get(agent) ?? 1) - 1;
    if (n > 0) { this.holders.set(agent, n); return false; }
    this.holders.delete(agent); this.views.delete(agent);
    return true;
  }
  private async attach(v: TerminalView): Promise<void> {
    const snap = await (this.state === 'open' ? this.call('terminal.attach', { agent: v.agent, history: 100 })
      : this.callNow('terminal.attach', { agent: v.agent, history: 100 }));
    v.setSnapshot(snap);
  }

  /** Fetches older scrollback into the view. Lines that scrolled off while attached arrive on a fresh attach. */
  async loadOlder(v: TerminalView, limit = 100): Promise<void> {
    const h = await this.call('terminal.history', { terminal: v.terminal, before: v.historyFirst, limit });
    v.prependHistory(h.first, h.lines.map((r) => r.text), h.more);
  }

  // ---- session control ----

  startSession(agent: string, account?: string) { return this.call('session.start', { agent, account }); }
  restartSession(agent: string, fresh = false) { return this.call('session.restart', { agent, fresh }); }
  parkSession(agent: string) { return this.call('session.park', { agent }); }
  stopSession(agent: string, mode: 'exit' | 'close' | 'kill') { return this.call('session.stop', { agent, mode }); }
  /** `enter: false` types without pressing Enter; Enter is its own keystroke. */
  sendInput(agent: string, text: string, enter = true) { return this.call('session.input', { agent, text, enter }); }
  sendKeys(agent: string, ...keys: string[]) { return this.call('session.keys', { agent, keys }); }
  interrupt(agent: string) { return this.call('session.interrupt', { agent }); }
  async openPrompts(agent: string): Promise<Prompt[]> { return (await this.call('prompt.list', { agent })).prompts; }
  /** Names the prompt answered: a changed prompt is refused as `stale_prompt` (data.current is the open one) and nothing is sent. */
  answerPrompt(agent: string, prompt: string, choice: string | { text: string }) {
    return this.call('prompt.answer', { agent, prompt, choice });
  }
  takeInput(agent: string) { return this.call('input.take', { agent }); }
  releaseInput(agent: string) { return this.call('input.release', { agent }); }
}
