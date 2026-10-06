// What the screens decide from daemon facts. Pure: no DOM, so tests run it in Node.
import type { AgentDetail, AgentView, InputHolder, Prompt, PromptOption } from '../src/protocol.ts';

export type Dot = 'running' | 'idle' | 'waiting' | 'blocked' | 'parked' | 'none';

/** The rail dot: the daemon's state word plus its block kinds. Unknown words read as idle. */
export function dotOf(a: AgentView): Dot {
  if (a.has_dialog || a.state === 'waiting') return 'waiting';
  if (a.block || a.state === 'stalled' || a.state === 'blocked') return 'blocked';
  if (a.state === 'no_terminal') return 'none';
  if (a.state === 'parked' || a.state === 'queued') return 'parked';
  if (a.state === 'running') return 'running';
  return 'idle';
}

const URGENCY: Record<Dot, number> = { waiting: 0, blocked: 1, running: 2, idle: 3, parked: 4, none: 5 };
export type GroupBy = 'role' | 'state' | 'account';

export interface RailGroup { title: string; rows: { agent: AgentView; depth: number }[] }

/** Groups agents; waiting ones sort up, and a child nests under the parent that spawned it. */
export function railGroups(agents: AgentView[], by: GroupBy, opts: { hideParked?: boolean; search?: string } = {}): RailGroup[] {
  const q = (opts.search ?? '').trim().toLowerCase();
  const shown = agents.filter((a) => !(opts.hideParked && dotOf(a) === 'parked')
    && (!q || `${a.name} ${a.role} ${a.account ?? ''} ${a.task?.title ?? ''}`.toLowerCase().includes(q)));
  const names = new Set(shown.map((a) => a.name));
  const key = (a: AgentView) => by === 'role' ? a.role : by === 'account' ? a.account ?? 'no account' : dotOf(a);
  const order = (a: AgentView, b: AgentView) => URGENCY[dotOf(a)] - URGENCY[dotOf(b)] || a.name.localeCompare(b.name);
  const groups = new Map<string, AgentView[]>();
  for (const a of shown) if (!a.parent || !names.has(a.parent)) (groups.get(key(a)) ?? groups.set(key(a), []).get(key(a))!).push(a);
  const out: RailGroup[] = [];
  for (const [title, members] of groups) {
    const rows: RailGroup['rows'] = [];
    const walk = (a: AgentView, depth: number) => {
      rows.push({ agent: a, depth });
      for (const c of shown.filter((x) => x.parent === a.name).sort(order)) walk(c, depth + 1);
    };
    for (const a of members.sort(order)) walk(a, 0);
    out.push({ title, rows });
  }
  const rank = (g: RailGroup) => Math.min(...g.rows.map((r) => URGENCY[dotOf(r.agent)]));
  return out.sort((a, b) => rank(a) - rank(b) || a.title.localeCompare(b.title));
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`;
}

export interface Controls { start: boolean; resume: boolean; stop: boolean; take: boolean; release: boolean; typing: boolean }

/** Which header buttons apply to this agent and this viewer. */
export function controlsOf(d: AgentDetail, me: string): Controls {
  const holder = d.input_holders[0];
  const iHold = !!holder && holder.kind !== 'daemon' && holder.name === me;
  return {
    start: !d.session_open && !d.session_id,
    resume: !d.session_open && !!d.session_id,
    stop: d.session_open,
    take: d.session_open && !iHold && (!holder || holder.kind === 'daemon'),
    release: iHold,
    typing: d.session_open && iHold,
  };
}

export const holderLabel = (h: InputHolder | undefined, me: string): string =>
  !h || h.kind === 'daemon' ? 'daemon' : h.name === me ? 'YOU' : h.name;

export type StopMode = 'interrupt' | 'exit' | 'close' | 'kill';
export const STOP_MODES: { mode: StopMode; label: string; hint: string }[] = [
  { mode: 'interrupt', label: 'Interrupt', hint: 'Esc: ends the turn' },
  { mode: 'exit', label: 'Exit', hint: '/exit' },
  { mode: 'close', label: 'Close', hint: 'ends the session, keeps terminal, worktree and account' },
  { mode: 'kill', label: 'Kill', hint: 'kills the terminal' },
];
/** Kill always asks; Close asks when the agent is mid-turn or waiting on someone. */
export const needsConfirm = (mode: StopMode, a: AgentView): boolean =>
  mode === 'kill' || (mode === 'close' && (a.state === 'running' || a.has_dialog));

/** Options a card offers: a permission prompt only ever offers yes and no, never "always". */
export function offered(p: Prompt): PromptOption[] {
  return p.kind === 'permission' ? p.options.filter((o) => o.answer === 'yes' || o.answer === 'no') : p.options;
}

export type KeyEvent = { key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean };
export type Typed = { kind: 'key'; name: string } | { kind: 'text'; text: string };

/** A browser key event as the daemon's input: a named key (`session.keys`) or printable text (`session.input`). */
export function typedFrom(e: KeyEvent, dialogOpen: boolean): Typed | null {
  if (e.metaKey || e.altKey) return null;
  if (e.ctrlKey) return e.key.toLowerCase() === 'c' ? { kind: 'key', name: 'ctrl-c' } : null;
  const named: Record<string, string> = { Escape: 'esc', Enter: 'enter', ArrowUp: 'up', ArrowDown: 'down', Tab: e.shiftKey ? 'shift-tab' : 'tab' };
  if (named[e.key]) return { kind: 'key', name: named[e.key] };
  if (e.key.length !== 1) return null;
  if (dialogOpen) return /^[0-9]$/.test(e.key) ? { kind: 'key', name: e.key } : null; // a dialog is a menu: only keys pass
  return { kind: 'text', text: e.key };
}

export type Dock = 'right' | 'left' | 'bottom' | 'full';
export interface PaneLayout { dock: Dock; open: boolean; tab: 'claude' | 'chat' }
export const DEFAULT_LAYOUT: PaneLayout = { dock: 'right', open: true, tab: 'claude' };

export interface KV { getItem(k: string): string | null; setItem(k: string, v: string): void }

/** The pane layout is remembered per agent in the browser; storage that throws or is empty gives the default. */
export function loadLayout(store: KV | null, agent: string): PaneLayout {
  try {
    const v = JSON.parse(store?.getItem(`layout:${agent}`) ?? 'null');
    if (v && ['right', 'left', 'bottom', 'full'].includes(v.dock)) return { ...DEFAULT_LAYOUT, ...v };
  } catch { /* fall through */ }
  return { ...DEFAULT_LAYOUT };
}
export function saveLayout(store: KV | null, agent: string, l: PaneLayout): void {
  try { store?.setItem(`layout:${agent}`, JSON.stringify(l)); } catch { /* storage may be blocked */ }
}
