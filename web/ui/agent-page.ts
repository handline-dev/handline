// Screen B: the agent page. Chat on one side (empty until the daemon serves chat), a movable pane with the live terminal on the other.
import type { AgentDetail, Prompt } from '@handline/client';
import { h, keyed } from './dom.ts';
import {
  ago, controlsOf, dotOf, holderLabel, loadLayout, needsConfirm, offered, saveLayout, STOP_MODES,
  type Dock, type KV, type PaneLayout, type StopMode,
} from '@handline/app-core';
import type { Store } from '@handline/app-core';
import { TerminalPane } from './terminal-pane.ts';

const CHIPS: [string, string][] = [['esc', 'Esc'], ['ctrl-c', '^C'], ['enter', '↵'], ['up', '↑'], ['down', '↓'], ['tab', 'Tab'], ['shift-tab', '⇧Tab'], ['1', '1'], ['2', '2'], ['3', '3']];
const DOCKS: [Dock, string, string][] = [['left', '◧', 'Dock left'], ['right', '◨', 'Dock right'], ['bottom', '⬓', 'Dock bottom'], ['full', '⛶', 'Fill the page']];

export class AgentPage {
  el: HTMLElement;
  private header = h('header', { class: 'agent-head' });
  private title = h('div', { class: 'agent-title' });
  private actions = h('div', { class: 'agent-actions' });
  private narrow = h('div', { class: 'narrow-tabs' });
  private banner = h('div', { class: 'banner-slot' });
  private paneHead = h('div', { class: 'pane-head' });
  private chips = h('div', { class: 'chips' });
  private cards = h('div', { class: 'cards' });
  private chatBox = h('section', { class: 'chat' });
  private paneBox = h('section', { class: 'pane-area' });
  private terminal: TerminalPane;
  private layout: PaneLayout;
  private account = ''; // one-off account override for the next start
  private off: () => void;
  private store: Store;
  private storage: KV | null;
  readonly agent: string;

  constructor(store: Store, agent: string, storage: KV | null) {
    this.store = store; this.agent = agent; this.storage = storage;
    this.layout = loadLayout(storage, agent);
    this.terminal = new TerminalPane(store, agent);
    this.header.append(this.title, this.actions, this.narrow, this.banner);
    this.el = h('div', { class: 'agent-page' }, this.header,
      h('div', { class: 'agent-body' }, this.chatBox, this.paneBox));
    this.buildChat(); this.buildPane();
    this.off = store.subscribe(() => this.render());
    void store.watch(agent).then(() => this.render());
    this.render();
  }

  dispose(): void { this.off(); this.store.unwatch(this.agent); void this.terminal.detach(); }

  private setLayout(patch: Partial<PaneLayout>): void {
    this.layout = { ...this.layout, ...patch }; saveLayout(this.storage, this.agent, this.layout); this.render();
  }

  // ---- chat ----

  private buildChat(): void {
    const box = h('textarea', { class: 'compose', rows: 2, placeholder: 'Chat is not available from this daemon yet', disabled: true, 'aria-label': 'Message to the agent' });
    this.chatBox.append(
      h('div', { class: 'stream' },
        h('div', { class: 'empty' }, h('strong', null, 'No chat yet'), h('p', null, 'This daemon does not serve the conversation. Use the terminal pane to work with the agent.'))),
      this.cards,
      h('div', { class: 'compose-row' }, box, h('button', { class: 'btn', disabled: true }, 'Send ↵')));
  }

  // ---- pane ----

  private buildPane(): void {
    this.paneBox.append(this.paneHead, this.terminal.el, this.chips);
  }

  private renderPane(d: AgentDetail | undefined): void {
    const l = this.layout, c = d ? controlsOf(d, this.store.me) : null;
    this.paneBox.hidden = !l.open;
    keyed(this.paneHead, l.dock, () => [
      h('div', { class: 'tabs', role: 'tablist' },
        h('button', { class: 'tab on', role: 'tab', 'aria-selected': 'true' }, 'Claude Code ',
          h('span', { class: 'x', title: 'Close the pane; the agent keeps running', onclick: () => this.setLayout({ open: false }) }, '✕')),
        h('button', { class: 'tab', role: 'tab', disabled: true, title: 'A shell pane needs the daemon to serve shell terminals' }, 'shell')),
      h('div', { class: 'docks' }, ...DOCKS.map(([dock, glyph, title]) =>
        h('button', { class: `icon${l.dock === dock ? ' on' : ''}`, title, 'aria-label': title, onclick: () => this.setLayout({ dock }) }, glyph)))]);
    const keysOn = !!d?.session_open && (!c || c.typing || c.take);
    keyed(this.chips, keysOn, () => CHIPS.map(([key, label]) =>
      h('button', { class: 'chip', disabled: !keysOn, title: `Send ${label}`, onclick: () => void this.press(key) }, label)));
    this.terminal.setDialog(!!d?.prompt || !!d?.has_dialog, !!d?.terminal);
  }

  /** A chip is typing, so it takes the keyboard first, then sends the key. */
  private async press(key: string): Promise<void> {
    const d = this.store.details.get(this.agent);
    if (!d) return;
    if (!controlsOf(d, this.store.me).typing && !(await this.store.act('take control', () => this.store.client.takeInput(this.agent)))) return;
    await this.store.act('key', () => this.store.client.sendKeys(this.agent, key));
  }

  // ---- header and cards ----

  private async stop(mode: StopMode): Promise<void> {
    const a = this.store.agents.get(this.agent);
    if (a && needsConfirm(mode, a) && !confirm(`${mode === 'kill' ? 'Kill the terminal of' : 'Close the session of'} ${this.agent}?${a.task ? ` It has work in flight: ${a.task.title}.` : ''}`)) return;
    await this.store.act(mode, () => mode === 'interrupt' ? this.store.client.interrupt(this.agent) : this.store.client.stopSession(this.agent, mode));
  }

  private card(p: Prompt): HTMLElement {
    const choose = (key: string) => void this.store.act('answer', () => this.store.client.answerPrompt(this.agent, p.id, key));
    const what = p.kind === 'permission' ? `Permission: ${p.tool ?? ''}` : `Dialog: ${p.kind}`;
    return h('div', { class: 'card prompt', role: 'alert' },
      h('div', { class: 'card-title' }, '⚠ ', what),
      p.command ? h('code', { class: 'cmd' }, p.command) : h('pre', { class: 'lines' }, p.lines.join('\n')),
      p.reason ? h('p', { class: 'muted' }, p.reason) : null,
      h('div', { class: 'card-actions' }, ...offered(p).map((o) =>
        h('button', { class: `btn${o.answer === 'yes' ? ' primary' : ''}`, onclick: () => choose(o.key) }, `${o.label} `, h('kbd', null, o.key)))));
  }

  private render(): void {
    const a = this.store.agents.get(this.agent), d = this.store.details.get(this.agent);
    if (!a) {
      keyed(this.title, this.store.conn, () => [h('p', { class: 'muted' }, this.store.conn === 'open' ? `No agent named ${this.agent}.` : 'Connecting…')]);
      this.actions.replaceChildren(); this.actions.dataset.sig = '';
      return;
    }
    const c = d ? controlsOf(d, this.store.me) : null;
    const accounts = [...new Set([...this.store.agents.values()].map((x) => x.account).filter((x): x is string => !!x))];
    // Each part is rebuilt only when what it shows changed, so an open Stop menu or account list survives other agents' events.
    keyed(this.title, [a.state, a.state_since, a.model, a.account, a.task, a.role, d?.worktree?.branch, dotOf(a)], () => [
      h('span', { class: `dot ${dotOf(a)}`, title: a.state }), h('strong', null, a.name),
      h('span', { class: 'muted' }, [a.model, a.account, a.task?.title, d?.worktree ? `worktree ${d.worktree.branch}` : null].filter(Boolean).join(' · ')),
      h('span', { class: 'state' }, `${a.state} ${ago(a.state_since)}`)]);
    keyed(this.actions, [c, accounts, d?.account_pick, d?.input_holders, this.store.me, this.layout.open, this.account], () => [
      c?.start ? h('button', { class: 'btn primary', onclick: () => void this.store.act('start', () => this.store.client.startSession(this.agent, this.account || undefined)) }, '▶ Start') : null,
      c?.start ? h('select', { 'aria-label': 'Account for this start', title: d ? `The daemon would pick: ${d.account_pick.account ?? 'none'} (${d.account_pick.reason})` : '',
        onchange: (e: Event) => { this.account = (e.target as HTMLSelectElement).value; } },
        h('option', { value: '' }, `account: ${d?.account_pick.account ?? 'auto'}`), ...accounts.map((x) => h('option', { value: x, selected: x === this.account }, x))) : null,
      c?.resume ? h('button', { class: 'btn', onclick: () => void this.store.act('resume', () => this.store.client.restartSession(this.agent)) }, '⟳ Resume') : null,
      c?.stop ? h('details', { class: 'menu' }, h('summary', { class: 'btn' }, '■ Stop ▾'),
        h('div', { class: 'menu-list' }, ...STOP_MODES.map((m) =>
          h('button', { class: 'menu-item', onclick: (e: Event) => { (e.target as HTMLElement).closest('details')!.removeAttribute('open'); void this.stop(m.mode); } },
            h('strong', null, m.label), h('span', { class: 'muted' }, ` ${m.hint}`))))) : null,
      d ? h('span', { class: 'holder', title: 'Who may type now' }, `input: ${holderLabel(d.input_holders?.[0], this.store.me)}`) : null,
      c?.take ? h('button', { class: 'btn', onclick: () => void this.store.act('take control', () => this.store.client.takeInput(this.agent)) }, 'Take control') : null,
      c?.release ? h('button', { class: 'btn', onclick: () => void this.store.act('release', () => this.store.client.releaseInput(this.agent)) }, 'Release') : null,
      !this.layout.open ? h('button', { class: 'btn', onclick: () => this.setLayout({ open: true }) }, 'Show pane') : null]);
    keyed(this.narrow, this.layout.tab, () => (['chat', 'claude'] as const).map((t) =>
      h('button', { class: `tab${this.layout.tab === t ? ' on' : ''}`, onclick: () => this.setLayout({ tab: t, open: true }) }, t === 'chat' ? 'Chat' : 'Claude Code')));
    keyed(this.banner, [a.block, a.wall_reason], () => a.block
      ? [h('div', { class: 'banner' }, `Blocked: ${a.block.kind}${a.wall_reason ? ` (${a.wall_reason})` : ''} since ${ago(a.block.since)}`)] : []);
    keyed(this.cards, d?.prompt, () => d?.prompt ? [this.card(d.prompt)] : []);
    this.el.dataset.dock = this.layout.dock;
    this.el.dataset.tab = this.layout.tab;
    this.renderPane(d);
  }
}
