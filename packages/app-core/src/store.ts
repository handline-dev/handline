// What the apps keep of the daemon outside the query cache (`@handline/app-data`): the connection, who the caller is and what it may call, the
// error line, what happened to each agent while this page watched, and the one open agent page (phone). Screens subscribe to it.
import type { DaemonClient } from '@handline/client';
import { RpcError, type ConnectionState, type Event as WireEvent, type TerminalView } from '@handline/client';

export interface Note {
  at: string;
  kind: string;
  text: string;
  actor: string | null;
}
export type PageTab = 'chat' | 'terminal';
/** The agent a one-page app has open: which tab shows, and its attached terminal (its row and detail are `useAgentDetail(agent)`). */
export interface Page {
  agent: string;
  tab: PageTab;
  view: TerminalView;
}

export class Store {
  client: DaemonClient;
  notes = new Map<string, Note[]>(); // what happened to each agent while this page watched
  conn: ConnectionState = 'connecting';
  me = '';
  /** The methods this caller may use, from hello; a screen shows only what the daemon serves. */
  served = new Set<string>();
  error: string | null = null;
  page: Page | null = null;
  /** Counts changes; a UI binding reads it as the snapshot that tells it to draw again. */
  version = 0;
  private wired = false;
  private listeners = new Set<() => void>();
  private openSeq = 0; // the latest openAgent call: an earlier one that finishes late undoes itself
  private offs: (() => void)[] = [];
  private unwatchView: (() => void) | null = null;

  constructor(client: DaemonClient) {
    this.client = client;
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private emit() {
    this.version++;
    for (const fn of this.listeners) fn();
  }

  /** Whether the daemon lets this caller use the method; false for a daemon that does not serve it. */
  can(method: string): boolean {
    return this.served.has(method);
  }

  /** Connects and learns who the caller is and what it may call; safe to call again after a failure to reach the daemon. */
  async start(): Promise<void> {
    if (!this.wired) {
      this.wired = true;
      this.offs.push(
        this.client.onState((s) => {
          this.conn = s;
          this.emit();
        }),
      );
      this.offs.push(this.client.onEvent((e) => this.onEvent(e)));
    }
    const hello = await this.client.start();
    this.conn = this.client.state;
    this.me = hello.caller.name;
    this.served = new Set(hello.methods.filter((m) => m.allowed).map((m) => m.name));
    this.emit();
    await this.client.subscribe(['agents', 'notifications']);
  }

  /** Detaches from the daemon and closes the connection. */
  stop(): void {
    for (const off of this.offs) off();
    this.offs = [];
    this.wired = false;
    this.unwatchView?.();
    this.unwatchView = null;
    this.page = null;
    this.client.close(); // a restart begins clean; the client is closing, so nothing is detached
  }

  private onEvent(e: WireEvent): void {
    if (e.type === 'agent.removed' && e.agent && this.page?.agent === e.agent) {
      this.error = `${e.agent} was removed`;
      void this.closeAgent();
    }
    if (e.agent && e.type === 'agent.control') {
      const d = e.data as { at: string; kind: string; text: string; actor: { name: string } | null };
      const list = this.notes.get(e.agent) ?? [];
      list.push({ at: d.at, kind: d.kind, text: d.text, actor: d.actor?.name ?? null });
      this.notes.set(e.agent, list.slice(-200));
    }
    this.emit();
  }

  /** Says why a call the person asked for failed, with the gate's own sentence and hint. A lost connection says nothing here: the banner does, and the reconnect refetches. */
  reportError(what: string, e: unknown): void {
    if (e instanceof RpcError) {
      if (e.code === 'transport') return;
      this.error = `${what}: ${e.reason ?? e.message}${e.hint ? ` (${e.hint})` : ''}`;
    } else this.error = `${what}: ${(e as Error).message}`;
    this.emit();
  }

  dismissError(): void {
    this.error = null;
    this.emit();
  }
  /** Shows a sentence the store itself did not produce, such as an unreachable daemon. */
  fail(message: string): void {
    this.error = message;
    this.emit();
  }

  // ---- the open agent page (one-page apps) ----

  /** Opens the agent's page: attaches its terminal and sets the page. Two quick calls end with the later one open; the earlier one lets go of what it took. */
  async openAgent(agent: string): Promise<void> {
    const seq = ++this.openSeq;
    await this.closeAgent();
    if (seq !== this.openSeq) return;
    let attached = false;
    try {
      const view = await this.client.attachTerminal(agent);
      attached = true;
      if (seq !== this.openSeq) throw new Superseded();
      this.error = null;
      this.unwatchView = view.subscribe(() => this.emit());
      this.page = { agent, tab: 'chat', view };
      this.emit();
    } catch (e) {
      if (attached) await this.client.detachTerminal(agent).catch(() => {});
      if (!(e instanceof Superseded)) this.reportError('open', e);
    }
  }
  async closeAgent(): Promise<void> {
    const p = this.page;
    if (!p) return;
    this.unwatchView?.();
    this.unwatchView = null;
    this.page = null;
    this.emit();
    await this.client.detachTerminal(p.agent).catch(() => {});
  }
  setTab(tab: PageTab): void {
    if (this.page) {
      this.page = { ...this.page, tab };
      this.emit();
    }
  }
  loadOlder(): Promise<void> {
    const v = this.page?.view;
    if (!v) return Promise.resolve();
    return this.client.loadOlder(v).then(
      () => {},
      (e) => this.reportError('load older', e),
    );
  }
}

/** A later openAgent took over while this one was attaching. */
class Superseded extends Error {}
