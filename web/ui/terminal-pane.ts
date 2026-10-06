// The live text-grid terminal pane: renders a TerminalView, and sends what the person types once they hold the input.
import type { TerminalView } from '@handline/client';
import { h } from './dom.ts';
import { keyFrom } from '@handline/app-core';
import type { Store } from '@handline/app-core';

export class TerminalPane {
  el: HTMLElement;
  private grid: HTMLPreElement;
  private view: TerminalView | null = null;
  private off: (() => void) | null = null;
  private frame = 0;
  private input: HTMLTextAreaElement;
  private agent: string;
  private store: Store;
  private status: HTMLElement;
  private dialogOpen = false;
  private attaching = false;

  constructor(store: Store, agent: string) {
    this.store = store; this.agent = agent;
    this.grid = h('pre', { class: 'grid' });
    // Typing goes through a focused textarea, as in xterm: the browser's input and composition events carry IME text and paste.
    this.input = h('textarea', { class: 'grid-input', rows: 1, autocapitalize: 'off', autocomplete: 'off', spellcheck: 'false', 'aria-label': `Type into the terminal of ${agent}` });
    this.status = h('div', { class: 'grid-status' });
    this.el = h('div', { class: 'terminal' }, this.grid, this.status, this.input);
    this.input.addEventListener('keydown', (e) => void this.onKey(e));
    this.input.addEventListener('input', (e) => { if (!(e as InputEvent).isComposing) void this.drain(); });
    this.input.addEventListener('compositionend', () => void this.drain());
    this.grid.addEventListener('click', () => { if (!getSelection()?.toString()) this.input.focus(); });
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

  /** Whether this page holds the keyboard; says how to take it when not. */
  private holding(): boolean {
    const holder = this.store.details.get(this.agent)?.input_holders?.[0];
    const yes = !!holder && holder.kind !== 'daemon' && holder.name === this.store.me;
    if (!yes) this.status.textContent = 'Press "Take control" to type here; the daemon stops typing while you hold the keyboard.';
    return yes;
  }

  private async onKey(e: KeyboardEvent): Promise<void> {
    const k = keyFrom(e, this.dialogOpen);
    if (!k) return;
    e.preventDefault();
    if ('swallow' in k) return;
    if (!this.holding()) return;
    if ('unavailable' in k) { this.status.textContent = `The daemon cannot take ${k.unavailable} yet.`; return; }
    await this.drain();
    await this.store.act('key', () => this.store.client.sendKeys(this.agent, k.name));
  }

  /** Sends what the textarea collected (typed, pasted or composed) as text without Enter; Enter is its own key. */
  private async drain(): Promise<void> {
    const text = this.input.value; this.input.value = '';
    if (!text || this.dialogOpen || !this.holding()) return; // a dialog is a menu: only keys pass
    await this.store.act('typing', () => this.store.client.sendInput(this.agent, text, false));
  }
}
