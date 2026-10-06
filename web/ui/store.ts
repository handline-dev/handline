// The app's view of the daemon: agents, the open agent's detail, and what changed. Screens subscribe to it.
import { DaemonClient, RpcError, type AgentDetail, type AgentView, type ConnectionState, type Event as WireEvent } from '@handline/client';

export interface Note { at: string; kind: string; text: string; actor: string | null }

export class Store {
  client: DaemonClient;
  agents = new Map<string, AgentView>();
  details = new Map<string, AgentDetail>();
  notes = new Map<string, Note[]>(); // what happened to each agent while this page watched
  conn: ConnectionState = 'connecting';
  me = '';
  chat = false;
  error: string | null = null;
  private wired = false;
  private listeners = new Set<() => void>();
  private watched = new Set<string>(); // agents whose detail is kept fresh
  private pendingDetail = new Set<string>();

  constructor(client: DaemonClient) { this.client = client; }

  subscribe(fn: () => void): () => void { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  private emit() { for (const fn of this.listeners) fn(); }

  /** Connects and loads; safe to call again after a failure to reach the daemon. */
  async start(): Promise<void> {
    if (!this.wired) {
      this.wired = true;
      this.client.onState((s) => {
        this.conn = s; this.emit();
        if (s === 'open') void this.refresh().catch(() => {});
      });
      this.client.onEvent((e) => this.onEvent(e));
      this.client.onGap(() => { void this.refresh().catch(() => {}); });
    }
    const hello = await this.client.start();
    this.conn = this.client.state; this.me = hello.caller.name; this.chat = hello.capabilities.chat;
    await this.client.subscribe(['agents', 'notifications']);
    await this.refresh();
  }

  /** Fetches the agent list again, and the detail of every agent being watched. */
  async refresh(): Promise<void> {
    const { agents } = await this.client.call('agents.list', {});
    this.agents = new Map(agents.map((a) => [a.name, a]));
    await Promise.all([...this.watched].map((n) => this.loadDetail(n)));
    this.emit();
  }

  /** Keeps this agent's detail current until `unwatch`. */
  async watch(name: string): Promise<void> { this.watched.add(name); await this.loadDetail(name); this.emit(); }
  unwatch(name: string): void { this.watched.delete(name); }

  private async loadDetail(name: string): Promise<void> {
    try { this.details.set(name, await this.client.call('agent.get', { agent: name })); }
    catch (e) { if (e instanceof RpcError && e.code === 'not_found') this.details.delete(name); else throw e; }
  }

  private onEvent(e: WireEvent): void {
    if (e.type === 'agent.changed') this.agents.set(e.data.name as string, e.data as unknown as AgentView);
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

  /** Runs a call the person asked for; a refusal is shown with the gate's own sentence and hint. */
  async act<T>(what: string, fn: () => Promise<T>): Promise<T | undefined> {
    try { const r = await fn(); this.error = null; return r; }
    catch (e) {
      if (e instanceof RpcError) {
        this.error = `${what}: ${e.reason ?? e.message}${e.hint ? ` (${e.hint})` : ''}`;
        if (e.code === 'stale_prompt') { const a = (e.data?.current as { agent?: string } | null)?.agent; if (a) void this.loadDetail(a).then(() => this.emit()); }
      } else this.error = `${what}: ${(e as Error).message}`;
      this.emit();
      return undefined;
    }
  }
}
