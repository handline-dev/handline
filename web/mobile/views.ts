// Pure drawing: state in, HTML string out. Controls carry data-act (and data-* arguments) that app.ts
// delegates, so nothing here touches the DOM and tests read the strings.
import type { AgentDetail, AgentView, Prompt } from '../src/protocol.ts';
import { KEY_STRIP, type State } from './store.ts';

export const esc = (s: unknown): string =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

const STATE_DOT: Record<string, string> = { running: 'run', waiting: 'wait', idle: 'idle', parked: 'off', no_terminal: 'off' };
const dot = (state: string) => `<span class="dot ${STATE_DOT[state] ?? 'off'}" aria-hidden="true"></span>`;

function agentRow(a: AgentView): string {
  const sub = [a.task?.title, a.account && `acct ${a.account}`, a.model].filter(Boolean).map(esc).join(' · ');
  const flag = a.has_dialog ? '<span class="flag">asks</span>' : a.block ? `<span class="flag warn">${esc(a.block.kind)}</span>` : '';
  return `<li><button class="row" data-act="open" data-agent="${esc(a.name)}">${dot(a.state)}
    <span class="grow"><b>${esc(a.name)}</b><small>${esc(a.state)}${sub ? ' · ' + sub : ''}</small></span>${flag}</button></li>`;
}

export function agentList(s: State): string {
  const rows = s.agents.length ? s.agents.map(agentRow).join('') : '<li class="empty">No agents.</li>';
  return `<header class="bar"><h1>Agents</h1></header><ul class="list" data-r="list">${rows}</ul>`;
}

export function permissionCard(p: Prompt): string {
  const what = p.command ? `<code>${esc(p.tool ?? '')} ${esc(p.command)}</code>` : p.lines.map((l) => `<div>${esc(l)}</div>`).join('');
  // Yes/No only where the daemon marked the answer; other options stay as plain key chips.
  const buttons = p.options.map((o) =>
    `<button class="btn ${o.answer ?? ''}" data-act="answer" data-key="${esc(o.key)}" data-prompt="${esc(p.id)}">${esc(o.label)}</button>`).join('');
  return `<section class="card prompt"><h3>${esc(p.kind === 'permission' ? 'Permission' : p.kind)}${p.reason ? ' · ' + esc(p.reason) : ''}</h3>${what}<div class="actions">${buttons}</div></section>`;
}

function controls(d: AgentDetail): string {
  const b = (act: string, label: string, extra = '') => `<button class="btn" data-act="${act}" ${extra}>${label}</button>`;
  if (!d.session_open) {
    return d.session_id ? b('resume', 'Resume') + b('start', 'Start new') : b('start', 'Start');
  }
  return b('interrupt', 'Interrupt') + b('stop', 'Exit', 'data-mode="exit"') + b('stop', 'Close', 'data-mode="close"') + b('stop', 'Kill', 'data-mode="kill"');
}

// Chat has no daemon method to draw from yet (capabilities.chat is false in the fake), so this is its empty state.
function chatTab(s: State): string {
  const card = s.page!.detail.prompt ? permissionCard(s.page!.detail.prompt) : '';
  return `<div data-r="card">${card}</div><div class="empty"><b>Chat is not available yet</b><small>This daemon does not serve chat. Use the Terminal tab to talk to the agent.</small></div>`;
}

function terminalTab(s: State): string {
  const p = s.page!;
  const lines = p.view.text();
  const older = p.view.historyMore ? '<button class="btn link" data-act="older">Load older</button>' : '';
  const keys = KEY_STRIP.map((k) => `<button class="key" data-act="key" data-key="${esc(k.key)}">${esc(k.label)}</button>`).join('');
  const card = p.detail.prompt ? permissionCard(p.detail.prompt) : '';
  const holder = p.detail.input_holders.map((h) => (h.kind === 'device' ? 'PHONE' : h.kind)).join(', ');
  // data-r regions are patched in place by app.ts; the composer and the key strip have none, so they live as long as the tab.
  return `<div data-r="card">${card}</div><div data-r="older">${older}</div>
    <pre class="grid" data-r="grid" aria-label="Terminal">${esc(lines.join('\n')) || '<span class="muted">No terminal output.</span>'}</pre>
    <form class="composer" data-act="type"><input name="text" autocomplete="off" autocapitalize="off" placeholder="Type to the terminal" /><button class="btn">Send</button></form>
    <div class="keys">${keys}</div><small class="muted" data-r="holder">${holder ? `Input: ${esc(holder)}` : ''}</small>`;
}

export function agentPage(s: State): string {
  const p = s.page!;
  const d = p.detail;
  const sub = [d.state, d.account && `acct ${d.account}`, d.model].filter(Boolean).map(esc).join(' · ');
  const tab = (t: 'chat' | 'terminal', label: string) =>
    `<button role="tab" aria-selected="${p.tab === t}" class="${p.tab === t ? 'on' : ''}" data-act="tab" data-tab="${t}">${label}</button>`;
  return `<header class="bar"><button class="back" data-act="back" aria-label="Back">‹</button>
      <span class="grow" data-r="head"><h1>${dot(d.state)}${esc(p.agent)}</h1><small>${sub}</small></span></header>
    <div class="controls" data-r="controls">${controls(d)}</div>
    <nav class="tabs" role="tablist">${tab('chat', 'Chat')}${tab('terminal', 'Terminal')}</nav>
    <div class="tabpanel">${p.tab === 'chat' ? chatTab(s) : terminalTab(s)}</div>`;
}

/** What is on screen apart from its text: the page, or the agent and tab. A change of key redraws everything. */
export const pageKey = (s: State): string => (s.page ? `agent:${s.page.agent}:${s.page.tab}` : 'list');

export function app(s: State): string {
  const banner = s.connection === 'open' ? '' : `<div class="banner">${s.connection === 'closed' ? 'Disconnected' : 'Reconnecting…'}</div>`;
  const error = s.error ? `<div class="banner err" role="alert">${esc(s.error)}<button data-act="dismiss" aria-label="Dismiss">✕</button></div>` : '';
  const main = s.page ? agentPage(s) : agentList(s);
  // Destinations the design lists but this build does not have yet are shown disabled.
  const nav = ['Inbox', 'Agents', 'Tasks', 'Accounts', 'More']
    .map((n) => `<button class="${n === 'Agents' ? 'on' : ''}" ${n === 'Agents' ? 'data-act="back"' : 'disabled'}>${n}</button>`).join('');
  return `<div class="shell" data-key="${esc(pageKey(s))}"><div data-r="banner">${banner}</div><div data-r="error">${error}</div>
    <main>${main}</main><nav class="tabbar">${nav}</nav></div>`;
}
