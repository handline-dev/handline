// What the screens decide from daemon facts. Pure: no DOM, so tests run it in Node.
import type { AgentDetail, AgentView, InputHolder, Prompt, PromptOption } from '@handline/client';
import { DAEMON_KEYS, NAMED_KEYS } from './keys.ts';

export type Dot = 'running' | 'waking' | 'idle' | 'waiting' | 'blocked' | 'parked' | 'none';

/** The rail dot: the daemon's state word plus its block kinds. Unknown words read as idle. */
export function dotOf(a: Pick<AgentView, 'state' | 'has_dialog' | 'block'>): Dot {
  if (a.has_dialog || a.state === 'waiting') return 'waiting';
  if (a.block || a.state === 'stalled' || a.state === 'blocked') return 'blocked';
  if (a.state === 'no_terminal') return 'none';
  if (a.state === 'queued') return 'waking';
  if (a.state === 'parked') return 'parked';
  if (a.state === 'running') return 'running';
  return 'idle';
}

const URGENCY: Record<Dot, number> = { waiting: 0, blocked: 1, running: 2, waking: 3, idle: 4, parked: 5, none: 6 };
export interface RailRow {
  agent: AgentView;
  depth: number;
  /** How many shown agents are its children: a row with some has a fold button. */
  kids: number;
}

/** The team an agent belongs to: the part of its name before the first dot. A bare name is its own team when others carry it as a prefix, else it stands in "agents". */
export function teamOf(name: string, names: readonly string[]): string {
  const dot = name.indexOf('.');
  if (dot > 0) return name.slice(0, dot);
  return names.some((n) => n.startsWith(`${name}.`)) ? name : 'agents';
}

/** What a row shows of the name: inside its team, the part after the team's prefix. */
export const shortName = (name: string, team: string): string => (name.startsWith(`${team}.`) ? name.slice(team.length + 1) : name);

/** The agent list as one list: waiting ones sort up, and a child nests under the parent that spawned it. `hidden` lists the dots the filter menu has switched off. */
export function railRows(agents: AgentView[], opts: { hidden?: ReadonlySet<Dot>; search?: string } = {}): RailRow[] {
  const q = (opts.search ?? '').trim().toLowerCase();
  const shown = agents.filter(
    (a) => !opts.hidden?.has(dotOf(a)) && (!q || `${a.name} ${a.role} ${a.account ?? ''} ${a.task?.title ?? ''}`.toLowerCase().includes(q)),
  );
  const names = new Set(shown.map((a) => a.name));
  const order = (a: AgentView, b: AgentView) => URGENCY[dotOf(a)] - URGENCY[dotOf(b)] || a.name.localeCompare(b.name);
  const rows: RailRow[] = [];
  const walk = (a: AgentView, depth: number) => {
    const kids = shown.filter((x) => x.parent === a.name).sort(order);
    rows.push({ agent: a, depth, kids: kids.length });
    for (const c of kids) walk(c, depth + 1);
  };
  for (const a of shown.filter((x) => !x.parent || !names.has(x.parent)).sort(order)) walk(a, 0);
  return rows;
}

/** The rows left once the collapsed agents hide their descendants: a row is dropped while it sits deeper than the nearest collapsed row above it. */
export function visibleRows<R extends { agent: AgentView; depth: number }>(rows: R[], collapsed: ReadonlySet<string>): R[] {
  const out: R[] = [];
  let hideBelow: number | null = null;
  for (const r of rows) {
    if (hideBelow !== null && r.depth > hideBelow) continue;
    hideBelow = collapsed.has(r.agent.name) ? r.depth : null;
    out.push(r);
  }
  return out;
}

/** The filter menu's choices: what a person asks of the list, each over the rail dots it covers. A dot no choice names is always shown. */
export const RAIL_FILTERS: { id: string; label: string; dots: Dot[] }[] = [
  { id: 'needs', label: 'Needs me', dots: ['waiting', 'blocked'] },
  { id: 'working', label: 'Working', dots: ['running'] },
  { id: 'waking', label: 'Waking', dots: ['waking'] },
  { id: 'idle', label: 'Idle', dots: ['idle'] },
  { id: 'sleeping', label: 'Sleeping', dots: ['parked', 'none'] },
];

/** The word a row says for its state; a person's wait and a block outrank the plain states. */
export const RAIL_STATE_WORD: Record<Dot, string> = {
  waiting: 'needs you',
  blocked: 'blocked',
  running: 'working',
  waking: 'waking',
  idle: 'idle',
  parked: 'sleeping',
  none: 'sleeping',
};

/** What is piled up for an agent: tasks it owns that are not done (`task_count`) and messages owed to it that have not reached it (`unreached`, which the daemon sends only for agents.list `status: true`). A count the view does not carry is null, never 0. */
export function queueOf(a: AgentView, unreached: number | null = null): { tasks: number | null; messages: number | null } {
  return { tasks: a.task_count ?? null, messages: a.unreached ?? unreached };
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`;
}

export interface Controls {
  start: boolean;
  resume: boolean;
  stop: boolean;
  take: boolean;
  release: boolean;
  typing: boolean;
  /** A structured agent whose turn runs or waits; a terminal agent interrupts through the stop menu. */
  interrupt: boolean;
}

/** Which header buttons apply to this agent and this viewer. */
export function controlsOf(d: AgentDetail, me: string): Controls {
  if (d.structured) {
    // No terminal, so nobody holds its input: it is started, woken, stopped and interrupted, never typed to.
    return {
      start: !d.session_open && !d.session_id,
      resume: !d.session_open && !!d.session_id,
      stop: d.session_open,
      take: false,
      release: false,
      typing: false,
      interrupt: d.session_open && (d.state === 'running' || d.state === 'waiting'),
    };
  }
  const holder = d.input_holders?.[0];
  const iHold = !!holder && holder.kind !== 'daemon' && holder.name === me;
  return {
    start: !d.session_open && !d.session_id,
    resume: !d.session_open && !!d.session_id,
    stop: d.session_open,
    take: d.session_open && !iHold && (!holder || holder.kind === 'daemon'),
    release: iHold,
    typing: d.session_open && iHold,
    interrupt: false,
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
/** The same modes for a structured agent, in session words: it has no terminal and Close and Exit end it the same way. */
export const STRUCTURED_STOP_MODES: { mode: StopMode; label: string; hint: string }[] = [
  { mode: 'interrupt', label: 'Interrupt', hint: 'ends the running turn' },
  { mode: 'close', label: 'Close', hint: 'ends the session, keeps worktree and account' },
  { mode: 'kill', label: 'Kill', hint: 'ends the session now, mid-turn' },
];
export const stopModesOf = (a: Pick<AgentView, 'structured'>) => (a.structured ? STRUCTURED_STOP_MODES : STOP_MODES);
/** Kill always asks; Close asks when the agent is mid-turn or waiting on someone. */
export const needsConfirm = (mode: StopMode, a: AgentView): boolean =>
  mode === 'kill' || (mode === 'close' && (a.state === 'running' || a.has_dialog));

/** An option that saves a standing rule ("Yes, and don't ask again"). */
export const isAlways = (o: PromptOption): boolean => /\balways\b|don.t ask again/i.test(o.label);

/** Options a card offers: a permission prompt only ever offers yes and no, never "always". */
export function offered(p: Prompt): PromptOption[] {
  return p.kind === 'permission' ? p.options.filter((o) => (o.answer === 'yes' || o.answer === 'no') && !isAlways(o)) : p.options;
}

/** The title of a prompt card. */
export function promptTitle(p: Prompt): string {
  if (p.kind === 'permission') return `Permission: ${p.tool ?? ''}`;
  if (p.kind === 'question') return 'Question';
  if (p.kind === 'dialog') return 'Dialog';
  return `Dialog: ${p.kind}`;
}

/** What the card offers: a permission only yes and no; a dialog its options, minus the "always" ones when no rule can be kept. */
export function promptChoices(p: Prompt, rules: boolean): PromptOption[] {
  if (p.kind === 'dialog' && !rules) return p.options.filter((o) => !isAlways(o));
  if (p.kind === 'permission') return offered(p);
  return p.options;
}

export type KeyEvent = { key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean; isComposing?: boolean };

/**
 * What a keydown in the terminal sends: a named key the daemon takes, or null to leave the event alone. Printable text
 * is not read here: it arrives through the input and compositionend events of the focused textarea, which is what makes
 * IME composition and paste work. `unavailable` is set when the person pressed a key the daemon cannot take yet.
 */
export function keyFrom(
  e: KeyEvent,
  dialogOpen: boolean,
  keys: ReadonlySet<string> = DAEMON_KEYS,
): { name: string } | { unavailable: string } | { swallow: true } | null {
  if (e.isComposing || e.key === 'Process' || e.key === 'Dead') return null;
  if (e.metaKey || e.altKey) return null;
  if (e.ctrlKey) return e.key.toLowerCase() === 'c' ? { name: 'ctrl-c' } : null;
  const name = e.key === 'Tab' ? (e.shiftKey ? 'shift-tab' : 'tab') : NAMED_KEYS[e.key];
  if (name) return keys.has(name) ? { name } : { unavailable: e.key };
  if (e.key.length !== 1) return null;
  if (!dialogOpen) return null; // printable text comes from the textarea's input event
  return /^[0-9]$/.test(e.key) ? { name: e.key } : { swallow: true }; // a dialog is a menu: only keys pass
}

export type Dock = 'right' | 'left' | 'bottom' | 'full';
export interface PaneLayout {
  dock: Dock;
  open: boolean;
  tab: 'claude' | 'chat';
}
export const DEFAULT_LAYOUT: PaneLayout = { dock: 'right', open: true, tab: 'claude' };

export interface KV {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
}

/** The pane layout is remembered per agent in the browser; storage that throws or is empty gives the default. */
export function loadLayout(store: KV | null, agent: string): PaneLayout {
  try {
    const v = JSON.parse(store?.getItem(`layout:${agent}`) ?? 'null');
    if (v && ['right', 'left', 'bottom', 'full'].includes(v.dock)) return { ...DEFAULT_LAYOUT, ...v };
  } catch {
    /* fall through */
  }
  return { ...DEFAULT_LAYOUT };
}
export function saveLayout(store: KV | null, agent: string, l: PaneLayout): void {
  try {
    store?.setItem(`layout:${agent}`, JSON.stringify(l));
  } catch {
    /* storage may be blocked */
  }
}
