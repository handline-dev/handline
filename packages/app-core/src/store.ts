// The apps' view of the daemon: agents, watched agents' detail, what changed, and the one open agent page (phone). Screens subscribe to it.
import { DaemonClient, RpcError, type AgentDetail, type AgentView, type ConnectionState, type Event as WireEvent, type Prompt, type TerminalView } from '@handline/client';

export interface Note { at: string; kind: string; text: string; actor: string | null }
export type PageTab = 'chat' | 'terminal';
/** The agent a one-page app has open: which tab shows, and its attached terminal (detail is `details.get(agent)`). */
export interface Page { agent: string; tab: PageTab; view: TerminalView }

export class Store {
  client: DaemonClient;
  agents = new Map<string, AgentView>();
  details = new Map<string, AgentDetail>();
  notes = new Map<string, Note[]>(); // what happened to each agent while this page watched
  conn: ConnectionState = 'connecting';
  me = '';
  chat = false;
  error: string | null = null;
  page: Page | null = null;
  /** Counts changes; a UI binding reads it as the snapshot that tells it to draw again. */
  version = 0;
  private wired = false;
  private listeners = new Set<() => void>();
  private watched = new Map<string, number>(); // agents whose detail is kept fresh, and how many screens watch each
  private openSeq = 0; // the latest openAgent call: an earlier one that finishes late undoes itself
  private openTarget = '';
  private pendingDetail = new Set<string>();
  private offs: (() => void)[] = [];
  private unwatchView: (() => void) | null = null;

  constructor(client: DaemonClient) { this.client = client; }

  subscribe(fn: () => void): () => void { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  private emit() { this.version++; for (const fn of this.listeners) fn(); }

  /** Connects and loads; safe to call again after a failure to reach the daemon. */
  async start(): Promise<void> {
    if (!this.wired) {
      this.wired = true;
      this.offs.push(this.client.onState((s) => {
        this.conn = s; this.emit();
        if (s === 'open') void this.refresh().catch(() => {});
      }));
      this.offs.push(this.client.onEvent((e) => this.onEvent(e)));
      this.offs.push(this.client.onGap(() => { void this.refresh().catch(() => {}); }));
    }
    const hello = await this.client.start();
    this.conn = this.client.state; this.me = hello.caller.name; this.chat = hello.capabilities.chat;
    await this.client.subscribe(['agents', 'notifications']);
    await this.refresh();
  }

  /** Detaches from the daemon and closes the connection. */
  stop(): void {
    for (const off of this.offs) off();
    this.offs = []; this.wired = false;
    this.unwatchView?.(); this.unwatchView = null;
    this.page = null; this.watched.clear(); this.pendingDetail.clear(); // a restart begins clean; the client is closing, so nothing is detached
    this.client.close();
  }

  /** Fetches the agent list again, and the detail of every agent being watched. */
  async refresh(): Promise<void> {
    const { agents } = await this.client.call('agents.list', {});
    this.agents = new Map(agents.map((a) => [a.name, a]));
    this.emit();
    try { await Promise.all([...this.watched.keys()].map((n) => this.loadDetail(n))); } finally { this.emit(); }
  }

  /** Keeps this agent's detail current until `unwatch`. */
  async watch(name: string): Promise<void> { this.watched.set(name, (this.watched.get(name) ?? 0) + 1); await this.loadDetail(name); this.emit(); }
  /** Ends one `watch`; the detail is kept fresh until every watcher has let go. */
  unwatch(name: string): void {
    const n = (this.watched.get(name) ?? 0) - 1;
    if (n > 0) this.watched.set(name, n); else this.watched.delete(name);
  }

  private async loadDetail(name: string): Promise<void> {
    try { this.details.set(name, await this.client.call('agent.get', { agent: name })); }
    catch (e) {
      if (!(e instanceof RpcError && e.code === 'not_found')) throw e;
      this.details.delete(name);
      if (this.page?.agent === name) { this.error = `${name} is gone`; void this.closeAgent(); }
    }
  }

  private onEvent(e: WireEvent): void {
    if (e.type === 'agent.changed') this.agents.set(e.data.name as string, e.data as unknown as AgentView);
    if (e.type === 'agent.removed' && e.agent) {
      this.agents.delete(e.agent);
      if (this.page?.agent === e.agent) { this.error = `${e.agent} was removed`; void this.closeAgent(); }
    }
    if (e.agent && e.type === 'agent.control') {
      const d = e.data as { at: string; kind: string; text: string; actor: { name: string } | null };
      const list = this.notes.get(e.agent) ?? [];
      list.push({ at: d.at, kind: d.kind, text: d.text, actor: d.actor?.name ?? null });
      this.notes.set(e.agent, list.slice(-200));
    }
    if (e.agent && this.watched.has(e.agent) && !this.pendingDetail.has(e.agent)) {
      this.pendingDetail.add(e.agent);
      queueMicrotask(() => {
        this.pendingDetail.delete(e.agent!);
        void this.loadDetail(e.agent!).then(() => this.emit()).catch(() => {});
      });
    }
    this.emit();
  }

  /** Runs a call the person asked for; a refusal is shown with the gate's own sentence and hint. A lost connection says nothing here: the banner does, and the reconnect refetches. */
  async act<T>(what: string, fn: () => Promise<T>): Promise<T | undefined> {
    try { const r = await fn(); this.error = null; return r; }
    catch (e) {
      if (e instanceof RpcError) {
        if (e.code === 'transport') return undefined;
        this.error = `${what}: ${e.reason ?? e.message}${e.hint ? ` (${e.hint})` : ''}`;
        if (e.code === 'stale_prompt') { const a = (e.data?.current as { agent?: string } | null)?.agent; if (a) void this.loadDetail(a).then(() => this.emit()); }
      } else this.error = `${what}: ${(e as Error).message}`;
      this.emit();
      return undefined;
    }
  }

  dismissError(): void { this.error = null; this.emit(); }
  /** Shows a sentence the store itself did not produce, such as an unreachable daemon. */
  fail(message: string): void { this.error = message; this.emit(); }

  // ---- the open agent page (one-page apps) ----

  /** Opens the agent's page. Two quick calls end with the later one open; the earlier one lets go of what it took. */
  async openAgent(agent: string): Promise<void> {
    const seq = ++this.openSeq;
    this.openTarget = agent;
    await this.closeAgent();
    if (seq !== this.openSeq) return;
    let watched = false, attached = false, opened = false;
    await this.act('open', async () => {
      await this.watch(agent); watched = true;
      const view = await this.client.attachTerminal(agent); attached = true;
      if (seq !== this.openSeq) return;
      this.unwatchView = view.subscribe(() => this.emit());
      this.page = { agent, tab: 'chat', view };
      opened = true;
      this.emit();
    });
    if (opened) return;
    if (watched) this.unwatch(agent);
    if (attached && this.openTarget !== agent) await this.client.detachTerminal(agent).catch(() => {});
  }
  async closeAgent(): Promise<void> {
    const p = this.page;
    if (!p) return;
    this.unwatchView?.(); this.unwatchView = null;
    this.unwatch(p.agent);
    this.page = null;
    this.emit();
    await this.client.detachTerminal(p.agent).catch(() => {});
  }
  setTab(tab: PageTab): void { if (this.page) { this.page = { ...this.page, tab }; this.emit(); } }

  // ---- actions on an agent: the daemon's refusal reaches `error`, and the agent's detail is read again afterwards ----

  private async run(what: string, agent: string, fn: () => Promise<unknown>): Promise<void> {
    await this.act(what, fn);
    if (this.watched.has(agent)) { await this.loadDetail(agent).catch(() => {}); this.emit(); }
  }
  startAgent(agent: string) { return this.run('start', agent, () => this.client.startSession(agent)); }
  resume(agent: string) { return this.run('resume', agent, () => this.client.restartSession(agent)); }
  stopAgent(agent: string, mode: 'exit' | 'close' | 'kill') { return this.run(mode, agent, () => this.client.stopSession(agent, mode)); }
  send(agent: string, text: string) { return text.trim() ? this.run('type', agent, () => this.client.sendInput(agent, text)) : Promise.resolve(); }
  key(agent: string, key: string) { return this.run('key', agent, () => this.client.sendKeys(agent, key)); }
  interrupt(agent: string) { return this.run('interrupt', agent, () => this.client.interrupt(agent)); }
  answer(prompt: Prompt, key: string) { return this.run('answer', prompt.agent, () => this.client.answerPrompt(prompt.agent, prompt.id, key)); }
  take(agent: string) { return this.run('take control', agent, () => this.client.takeInput(agent)); }
  release(agent: string) { return this.run('release', agent, () => this.client.releaseInput(agent)); }
  loadOlder() { const v = this.page?.view; return v ? this.act('load older', () => this.client.loadOlder(v)).then(() => {}) : Promise.resolve(); }
}
