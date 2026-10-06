import {
  PROTOCOL, WRITE_METHODS,
  type ErrorCode, type EventNotice, type FrameNotice, type GapNotice, type HelloResult, type MarkerNotice,
  type MethodName, type Methods, type NamedKey, type Prompt, type ResizeNotice, type RpcResponse,
  type SessionStopMode, type TerminalSnapshot, type Topic,
} from './protocol.ts';
import type { Transport } from './transport.ts';

export class RpcError extends Error {
  // `transport` and `protocol` are the client's own: no connection, and a daemon it must not talk to.
  code: ErrorCode | 'transport' | 'protocol';
  reason?: string;
  constructor(code: ErrorCode | 'transport' | 'protocol', message: string, reason?: string) {
    super(message);
    this.code = code; this.reason = reason;
    this.name = 'RpcError';
  }
}

export type ConnectionState = 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface ClientOptions {
  connect: () => Promise<Transport>;
  client: string; // who is calling; the daemon names this actor on what it does
  reconnect?: { minMs: number; maxMs: number } | false;
  newRequestId?: () => string;
}

type Handler<T> = (value: T) => void;
interface Pending {
  id: number; method: string; params: unknown;
  resolve: (v: unknown) => void; reject: (e: Error) => void;
}

/** What a terminal pane renders: the visible rows, plus scrollback that scrolled off while attached. */
export class TerminalView {
  terminal = ''; seq = 0; cols = 0; rows = 0;
  frame: string[] = []; history: string[] = []; historyMore = false;
  markers: MarkerNotice[] = [];
  private listeners = new Set<() => void>();
  agent: string;
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
    this.frame = [...s.frame]; this.history = [...s.history]; this.historyMore = s.history_more;
    this.emit();
  }
  applyFrame(n: FrameNotice): void {
    this.history.push(...this.frame.slice(0, n.scrolled));
    this.frame = [...this.frame.slice(n.scrolled), ...Array(n.scrolled).fill('')];
    for (const r of n.rows) this.frame[r.row] = r.text;
    this.seq = n.seq; this.emit();
  }
  applyResize(n: ResizeNotice): void {
    this.cols = n.cols; this.rows = n.rows; this.seq = n.seq;
    this.frame = Array.from({ length: n.rows }, (_, i) => this.frame[i] ?? '');
    this.emit();
  }
  applyMarker(n: MarkerNotice): void { this.markers.push(n); this.seq = n.seq; this.emit(); }
  private emit(): void { for (const fn of this.listeners) fn(); }
}

export class DaemonClient {
  state: ConnectionState = 'connecting';
  hello: HelloResult | null = null;

  private transport: Transport | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private waitingForOpen: (() => void)[] = [];
  private topics = new Set<Topic>();
  private cursor: number | undefined;
  private eventHandlers = new Set<Handler<EventNotice>>();
  private gapHandlers = new Set<Handler<GapNotice>>();
  private stateHandlers = new Set<Handler<ConnectionState>>();
  private views = new Map<string, TerminalView>();
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
    const hello = await this.callNow('hello', { client: this.opts.client, protocol: PROTOCOL });
    if (hello.min_client > PROTOCOL || hello.protocol < PROTOCOL) {
      t.onclose = null; t.close();
      this.setState('closed'); this.closing = true;
      throw new RpcError('protocol',
        `The daemon speaks protocol ${hello.protocol} (needs client ${hello.min_client}+); this client speaks ${PROTOCOL}.`,
        'run `hl service install` to reload the service');
    }
    this.hello = hello;
    // Pending writes go first with their old ids and request_ids: a daemon that already did one
    // answers from its stored reply instead of repeating it.
    for (const p of this.pending.values()) this.transmit(p);
    await this.establish();
    this.setState('open');
    const waiters = this.waitingForOpen; this.waitingForOpen = [];
    for (const w of waiters) w();
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
  async call<M extends MethodName>(method: M, params: Methods[M]['params']): Promise<Methods[M]['result']> {
    if (this.closing) throw new RpcError('transport', 'client closed');
    if (this.state !== 'open') await new Promise<void>((res) => this.waitingForOpen.push(res));
    let p: Record<string, unknown> = params as Record<string, unknown>;
    if (WRITE_METHODS.has(method) && p.request_id === undefined) {
      p = { ...p, request_id: this.opts.newRequestId?.() ?? `${this.opts.client}-${Date.now().toString(36)}-${++this.requestCounter}` };
    }
    return new Promise((resolve, reject) => {
      const entry: Pending = { id: this.nextId++, method, params: p, resolve: resolve as Pending['resolve'], reject };
      this.pending.set(entry.id, entry);
      this.transmit(entry);
    });
  }

  private callNow<M extends MethodName>(method: M, params: Methods[M]['params']): Promise<Methods[M]['result']> {
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
    for (const p of this.pending.values()) p.reject(e);
    this.pending.clear(); this.inflightInternal.clear();
  }

  private receive(t: Transport, line: string): void {
    if (t !== this.transport) return;
    let msg: RpcResponse & { method?: string; params?: unknown };
    try { msg = JSON.parse(line); } catch { return; } // a line that is not JSON is not ours to act on
    if (msg.method && msg.id === undefined) { this.notice(msg.method, msg.params); return; }
    if (typeof msg.id !== 'number') return;
    const p = this.pending.get(msg.id);
    if (!p) return; // a reply to a resent call that already finished
    this.pending.delete(msg.id); this.inflightInternal.delete(msg.id);
    if (msg.error) {
      const e = msg.error;
      p.reject(new RpcError(e.data?.code ?? 'internal', e.message, e.data?.reason));
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
  onEvent(fn: Handler<EventNotice>): () => void { this.eventHandlers.add(fn); return () => this.eventHandlers.delete(fn); }
  /** The daemon dropped history this client needed: refetch what the events described. */
  onGap(fn: Handler<GapNotice>): () => void { this.gapHandlers.add(fn); return () => this.gapHandlers.delete(fn); }

  private notice(method: string, params: unknown): void {
    switch (method) {
      case 'event': {
        const e = params as EventNotice;
        if (this.cursor !== undefined && e.cursor <= this.cursor) return; // replayed twice
        this.cursor = e.cursor;
        for (const fn of this.eventHandlers) fn(e);
        break;
      }
      case 'gap': {
        const g = params as GapNotice;
        this.cursor = g.cursor;
        for (const fn of this.gapHandlers) fn(g);
        break;
      }
      case 'frame': case 'resize': case 'marker': {
        const n = params as { terminal: string; seq: number };
        const v = [...this.views.values()].find((x) => x.terminal === n.terminal);
        if (!v) return;
        if (!v.fits(n.seq)) { void this.attach(v).catch(() => {}); return; } // missed one: take a fresh frame
        if (method === 'frame') v.applyFrame(params as FrameNotice);
        else if (method === 'resize') v.applyResize(params as ResizeNotice);
        else v.applyMarker(params as MarkerNotice);
        break;
      }
    }
  }

  // ---- terminal channel ----

  /** Attaches a view that stays current, through reconnects, until detached. */
  async attachTerminal(agent: string): Promise<TerminalView> {
    const v = this.views.get(agent) ?? new TerminalView(agent);
    this.views.set(agent, v);
    try { await this.attach(v); } catch (e) { this.views.delete(agent); throw e; }
    return v;
  }
  async detachTerminal(agent: string): Promise<void> {
    const v = this.views.get(agent);
    if (!v) return;
    this.views.delete(agent);
    await this.call('terminal.detach', { terminal: v.terminal });
  }
  private async attach(v: TerminalView): Promise<void> {
    const snap = await (this.state === 'open' ? this.call('terminal.attach', { agent: v.agent })
      : this.callNow('terminal.attach', { agent: v.agent }));
    v.setSnapshot(snap);
  }

  // ---- session control ----

  startSession(agent: string, account?: string) { return this.call('session.start', { agent, account }); }
  stopSession(agent: string, mode: SessionStopMode) { return this.call('session.stop', { agent, mode }); }
  /** `enter: false` types without pressing Enter; Enter is its own keystroke. */
  sendInput(agent: string, text: string, enter = true) { return this.call('session.input', { agent, text, enter }); }
  sendKeys(agent: string, ...keys: NamedKey[]) { return this.call('session.keys', { agent, keys }); }
  interrupt(agent: string) { return this.call('session.interrupt', { agent }); }
  async openPrompts(agent: string): Promise<Prompt[]> { return (await this.call('prompt.list', { agent })).prompts; }
  /** Names the prompt answered: a changed prompt is refused as `stale_prompt` and nothing is sent. */
  answerPrompt(prompt: string, choice: NamedKey) { return this.call('prompt.answer', { prompt, choice }); }
  takeInput(agent: string) { return this.call('input.take', { agent }); }
  releaseInput(agent: string) { return this.call('input.release', { agent }); }
}
