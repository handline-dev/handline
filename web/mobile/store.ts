// The mobile app's state: the agent list and the open agent page, kept current from the daemon's
// events. No DOM here; views.ts draws it and app.ts wires it, so tests drive it against the fake daemon.
import { DaemonClient, RpcError, type AgentDetail, type AgentView, type Event, type Prompt, type TerminalView } from '@handline/client';

export type Tab = 'chat' | 'terminal';

export interface State {
  connection: DaemonClient['state'];
  chat: boolean; // the daemon serves chat.*
  agents: AgentView[];
  page: { agent: string; detail: AgentDetail; tab: Tab; view: TerminalView } | null;
  error: string | null;
}

/** Key names the daemon's session.keys takes, with the label on the key strip. */
export const KEY_STRIP: { label: string; key: string }[] = [
  { label: 'Esc', key: 'esc' }, { label: '^C', key: 'ctrl-c' }, { label: '↵', key: 'enter' },
  { label: '↑', key: 'up' }, { label: '↓', key: 'down' }, { label: '⇥', key: 'tab' }, { label: '⇧⇥', key: 'shift-tab' },
  { label: '1', key: '1' }, { label: '2', key: '2' }, { label: '3', key: '3' },
];

export class Store {
  state: State = { connection: 'connecting', chat: false, agents: [], page: null, error: null };
  private listeners = new Set<() => void>();
  private client: DaemonClient;
  private offs: (() => void)[] = [];
  private unwatchView: (() => void) | null = null;

  constructor(client: DaemonClient) { this.client = client; }

  subscribe(fn: () => void): () => void { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  private set(patch: Partial<State>): void {
    this.state = { ...this.state, ...patch };
    for (const fn of this.listeners) fn();
  }

  async start(): Promise<void> {
    this.offs.push(this.client.onState((s) => { this.set({ connection: s }); if (s === 'open') void this.refresh(); }));
    this.offs.push(this.client.onEvent((e) => this.onEvent(e)));
    this.offs.push(this.client.onGap(() => void this.refresh()));
    const hello = await this.client.start();
    this.set({ connection: this.client.state, chat: hello.capabilities.chat });
    await this.client.subscribe(['agents', 'notifications']);
    await this.refresh();
  }
  stop(): void { for (const off of this.offs) off(); this.offs = []; this.unwatchView?.(); this.client.close(); }

  /** Refetches what events describe; after a gap or a reconnect. */
  async refresh(): Promise<void> {
    await this.guard(async () => {
      this.set({ agents: (await this.client.call('agents.list', {})).agents });
      if (this.state.page) await this.reloadDetail(this.state.page.agent);
    });
  }

  private onEvent(e: Event): void {
    if (e.type === 'agent.changed') {
      const v = e.data as unknown as AgentView;
      const known = this.state.agents.some((a) => a.name === v.name);
      this.set({ agents: known ? this.state.agents.map((a) => (a.name === v.name ? v : a)) : [...this.state.agents, v] });
    } else if (e.type === 'agent.removed') {
      this.set({ agents: this.state.agents.filter((a) => a.name !== e.agent) });
    }
    // A change on the open agent can open, close or replace its prompt: ask the daemon which.
    const p = this.state.page;
    if (p && e.agent === p.agent && (e.topic === 'agents' || e.topic === 'notifications')) {
      void this.guard(() => this.reloadDetail(p.agent));
    }
  }

  private async reloadDetail(agent: string): Promise<void> {
    const detail = await this.client.call('agent.get', { agent });
    const p = this.state.page;
    if (p && p.agent === agent) this.set({ page: { ...p, detail } });
  }

  // ---- agent page ----

  async openAgent(agent: string): Promise<void> {
    await this.closeAgent();
    await this.guard(async () => {
      const detail = await this.client.call('agent.get', { agent });
      const view = await this.client.attachTerminal(agent);
      this.unwatchView = view.subscribe(() => this.set({}));
      this.set({ page: { agent, detail, tab: 'chat', view } });
    });
  }
  async closeAgent(): Promise<void> {
    const p = this.state.page;
    if (!p) return;
    this.unwatchView?.(); this.unwatchView = null;
    this.set({ page: null });
    await this.client.detachTerminal(p.agent).catch(() => {});
  }
  setTab(tab: Tab): void { if (this.state.page) this.set({ page: { ...this.state.page, tab } }); }

  // ---- actions: each reports the daemon's own sentence on refusal ----

  startAgent(agent: string) { return this.act(() => this.client.startSession(agent)); }
  resume(agent: string) { return this.act(() => this.client.restartSession(agent)); }
  stopAgent(agent: string, mode: 'exit' | 'close' | 'kill') { return this.act(() => this.client.stopSession(agent, mode)); }
  send(agent: string, text: string) { return text.trim() ? this.act(() => this.client.sendInput(agent, text)) : Promise.resolve(); }
  key(agent: string, key: string) { return this.act(() => this.client.sendKeys(agent, key)); }
  interrupt(agent: string) { return this.act(() => this.client.interrupt(agent)); }
  answer(prompt: Prompt, key: string) { return this.act(() => this.client.answerPrompt(prompt.agent, prompt.id, key)); }
  loadOlder() {
    const v = this.state.page?.view;
    return v ? this.act(() => this.client.loadOlder(v)) : Promise.resolve();
  }
  dismissError(): void { this.set({ error: null }); }

  private async act(fn: () => Promise<unknown>): Promise<void> {
    if (this.state.error) this.set({ error: null });
    await this.guard(async () => { await fn(); });
    const p = this.state.page;
    if (p) await this.guard(() => this.reloadDetail(p.agent));
  }
  private async guard(fn: () => Promise<void>): Promise<void> {
    try { await fn(); }
    catch (e) {
      if (!(e instanceof RpcError)) throw e;
      if (e.code === 'transport') return; // the connection banner says so, and the reconnect refetches
      // A stale prompt names the open one; the page reload shows it, so the sentence is enough.
      this.set({ error: e.hint ? `${e.message} (${e.hint})` : e.message });
    }
  }
}
