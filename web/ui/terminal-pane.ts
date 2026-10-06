// The live text-grid terminal pane: renders a TerminalView, and sends what the person types once they hold the input.
import type { TerminalView } from '../src/client.ts';
import { h } from './dom.ts';
import { typedFrom } from './model.ts';
import type { Store } from './store.ts';

export class TerminalPane {
  el: HTMLElement;
  private grid: HTMLPreElement;
  private view: TerminalView | null = null;
  private off: (() => void) | null = null;
  private frame = 0;
  private typing = '';
  private flushing = false;
  private agent: string;
  private store: Store;
  private status: HTMLElement;
  private dialogOpen = false;
  private attaching = false;

  constructor(store: Store, agent: string) {
    this.store = store; this.agent = agent;
    this.grid = h('pre', { class: 'grid', tabindex: 0, 'aria-label': `Claude Code terminal of ${agent}` });
    this.status = h('div', { class: 'grid-status' });
    this.el = h('div', { class: 'terminal' }, this.grid, this.status);
    this.grid.addEventListener('keydown', (e) => this.onKey(e));
    this.grid.addEventListener('scroll', () => {
      if (this.grid.scrollTop === 0 && this.view?.historyMore) void this.older();
    });
  }

  /** Attaches when the agent has a terminal; call again when that changes. */
  async attach(): Promise<void> {
    if (this.view || this.attaching) return;
    this.attaching = true;
    try {
      this.view = await this.store.client.attachTerminal(this.agent);
    } catch (e) {
      this.status.textContent = (e as { reason?: string; message: string }).reason ?? (e as Error).message;
      return;
    } finally { this.attaching = false; }
    this.status.textContent = '';
    this.off = this.view.subscribe(() => this.paint());
    this.paint(true);
  }

  async detach(): Promise<void> {
    this.off?.(); this.off = null;
    if (this.view) { this.view = null; await this.store.client.detachTerminal(this.agent).catch(() => {}); }
  }

  /** Called when the agent's detail changes: whether a dialog is open decides which typed keys pass. */
  setDialog(open: boolean, hasTerminal: boolean): void {
    this.dialogOpen = open;
    if (hasTerminal && !this.view) void this.attach();
    if (!hasTerminal && this.view) { void this.detach(); this.grid.textContent = ''; this.status.textContent = 'No terminal. Start the agent to open one.'; }
  }

  private paint(stick = false): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      if (!this.view) return;
      const g = this.grid, atBottom = stick || g.scrollHeight - g.scrollTop - g.clientHeight < 4;
      const before = g.scrollHeight;
      g.textContent = this.view.text().join('\n');
      if (atBottom) g.scrollTop = g.scrollHeight; else g.scrollTop += g.scrollHeight - before; // older lines arrived above
    });
  }

  private async older(): Promise<void> {
    const v = this.view; if (!v) return;
    await this.store.client.loadOlder(v).catch(() => {});
  }

  private async onKey(e: KeyboardEvent): Promise<void> {
    const t = typedFrom(e, this.dialogOpen);
    if (!t) return;
    e.preventDefault();
    const d = this.store.details.get(this.agent);
    const holder = d?.input_holders[0];
    if (!holder || holder.kind === 'daemon' || holder.name !== this.store.me) {
      this.status.textContent = 'Press "Take control" to type here; the daemon stops typing while you hold the keyboard.';
      return;
    }
    if (t.kind === 'key') { await this.flush(); await this.store.act('key', () => this.store.client.sendKeys(this.agent, t.name)); return; }
    this.typing += t.text;
    if (!this.flushing) { this.flushing = true; setTimeout(() => void this.flush(), 0); }
  }

  /** Typed characters go out together, without Enter; Enter is its own key. */
  private async flush(): Promise<void> {
    const text = this.typing; this.typing = ''; this.flushing = false;
    if (text) await this.store.act('typing', () => this.store.client.sendInput(this.agent, text, false));
  }
}
