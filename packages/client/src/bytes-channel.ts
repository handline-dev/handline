import { ByteTerminalView, toBase64, type ByteListener } from './byte-terminal-view.ts';
import type { CallParams } from './client.ts';
import type { MethodName, Methods, Notifications, TerminalBytesSnapshot } from './protocol.ts';

type Call = <M extends MethodName>(method: M, params: CallParams<M>) => Promise<Methods[M]['result']>;

/** The terminal channel in format `bytes`: one view per agent, kept current through reconnects (each reconnect attaches again and repaints). */
export class BytesChannel {
  private views = new Map<string, ByteTerminalView>();
  private holds = new Map<string, number>();
  private resyncing = new Set<ByteTerminalView>();
  private call: Call;
  private callNow: Call; // while the connection is being re-established, `call` would wait for it
  private reattachText: (agent: string) => Promise<void>; // a detach drops every attach of the terminal, so a text view still held attaches again
  constructor(call: Call, callNow: Call, reattachText: (agent: string) => Promise<void> = async () => {}) {
    this.call = call;
    this.callNow = callNow;
    this.reattachText = reattachText;
  }

  /**
   * Attaches, or joins the view another screen already holds; `on` hears the first repaint and everything after. Joining attaches
   * again, so every screen starts from a snapshot. Rejects when the daemon refuses (`unsupported`, `not_found`).
   */
  async open(agent: string, on: ByteListener): Promise<{ view: ByteTerminalView; off: () => void }> {
    const v = this.views.get(agent) ?? new ByteTerminalView(agent);
    this.views.set(agent, v);
    this.holds.set(agent, (this.holds.get(agent) ?? 0) + 1);
    const off = v.subscribe(on);
    try {
      await this.attach(v);
    } catch (e) {
      off();
      this.release(agent);
      throw e;
    }
    return { view: v, off };
  }
  async close(agent: string): Promise<void> {
    const v = this.views.get(agent);
    if (!v || !this.release(agent) || !v.terminal) return;
    await this.call('terminal.detach', { terminal: v.terminal });
    await this.reattachText(agent);
  }
  /** After a text detach: the bytes view, if one is still held, attaches again. */
  async reattach(agent: string): Promise<void> {
    const v = this.views.get(agent);
    if (v) await this.resync(v);
  }
  private release(agent: string): boolean {
    const n = (this.holds.get(agent) ?? 1) - 1;
    if (n > 0) {
      this.holds.set(agent, n);
      return false;
    }
    this.holds.delete(agent);
    this.views.delete(agent);
    return true;
  }

  private async attach(v: ByteTerminalView, connecting = false): Promise<void> {
    const snap = await (connecting ? this.callNow : this.call)('terminal.attach', { agent: v.agent, format: 'bytes', history: 2000 });
    if (snap.format !== 'bytes') throw new Error('the daemon answered a bytes attach with text');
    v.setSnapshot(snap as TerminalBytesSnapshot);
  }
  /** Attaches again and repaints from the new snapshot; a refusal is shown by the view, not thrown. */
  async resync(v: ByteTerminalView, connecting = false): Promise<void> {
    if (this.resyncing.has(v)) return; // one attach in flight per view
    this.resyncing.add(v);
    v.setState('attaching');
    try {
      await this.attach(v, connecting);
    } catch (e) {
      v.setState('failed', (e as { reason?: string }).reason ?? (e as Error).message);
    } finally {
      this.resyncing.delete(v);
    }
  }

  /** The connection dropped: the screens stay, dimmed, until the reconnect repaints them. */
  markStale(): void {
    for (const v of this.views.values()) v.setState('stale');
  }
  async reattachAll(): Promise<void> {
    await Promise.all([...this.views.values()].map((v) => this.resync(v, true)));
  }

  /** Routes one terminal notice to the view it belongs to; false when none does. */
  notice(method: string, params: unknown): boolean {
    if (method !== 'output' && method !== 'snapshot' && method !== 'marker' && method !== 'holder') return false;
    const n = params as Notifications['output'];
    const v = [...this.views.values()].find((x) => x.terminal === n.terminal);
    if (!v) return false;
    if (v.state === 'attaching') return true; // the reply to the attach in flight covers everything up to it
    if (v.apply(method, n) === 'resync') void this.resync(v);
    return true;
  }

  /** Keys typed or pasted in the pane, as the terminal sends them. The input holder only. */
  input(v: ByteTerminalView, data: Uint8Array) {
    return this.call('terminal.input', { terminal: v.terminal, data: toBase64(data) });
  }
  resize(v: ByteTerminalView, cols: number, rows: number) {
    return this.call('terminal.resize', { terminal: v.terminal, cols, rows });
  }
  async loadOlder(v: ByteTerminalView, limit = 500): Promise<void> {
    const h = await this.call('terminal.history', { terminal: v.terminal, before: v.historyFirst, limit, format: 'bytes' });
    v.prependHistory(h.first, h.lines as string[], h.more);
  }
}
