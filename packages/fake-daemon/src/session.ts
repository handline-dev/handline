import { PROTOCOL } from '@handline/contract';
import type { AgentDetail, AgentView, InputHolder, Methods, Notifications, Prompt, Row, Timestamp } from '@handline/contract';
import { BANNER, type AgentFixture } from './fixtures.ts';
import { refused } from './errors.ts';
import { defaultSettings } from './settings.ts';

export const ROWS = 24,
  COLS = 80;
export const DAEMON_HOLDER: InputHolder = { kind: 'daemon', name: 'daemon', since: null };
export const row = (text: string): Row => ({ text });

/** A frame as a styled watcher gets it: the same rows with their colour, marked with the attach's format. */
export function styledFrame(n: Notifications['frame'], cols: number): Notifications['frame'] {
  return {
    ...n,
    format: 'styled',
    lines: n.lines?.map((r) => styledRow(r.text, cols)),
    changed: n.changed?.map((c) => ({ ...c, row: styledRow(c.row.text, cols) })),
  };
}

/** A row with colour, the way `coded` does it for bytes: the prompt marker cyan, the agent's bullet green. */
export function styledRow(text: string, cols = Infinity): Row {
  text = [...text].slice(0, cols).join('');
  if (text.startsWith('> ')) return { text, spans: [{ from: 0, to: 1, style: { fg: 6 } }] };
  if (text.startsWith('●')) return { text, spans: [{ from: 0, to: 1, style: { fg: 2, bold: true } }] };
  return { text };
}

export interface Session {
  fx: AgentFixture;
  state: string;
  since: Timestamp;
  sessionId: string | null;
  open: boolean;
  hasTerminal: boolean;
  lines: string[];
  seq: number;
  cols: number;
  rows: number;
  buffer: string; // typed, Enter not yet pressed
  composing: boolean; // the last line shows the buffer
  holder: InputHolder;
  prompt: Prompt | null;
  structured?: boolean; // played as a structured session by the transcript
  queue: (() => void)[]; // the scripted turn, one step per tick
  counter: number;
  settings: Record<string, string>; // agent.set's values
  strip?: Partial<Methods['agent.strip']['result']>; // what a test sets over the numbers agent.strip answers
  received: { kind: 'text' | 'key'; value: string; actor: string }[]; // what reached the terminal
}

/** How one connection watches a terminal: the format it asked for, and its own notice counter when that is bytes. */
export interface Attach {
  format: 'text' | 'bytes' | 'styled';
  seq: number;
}

export const tid = (s: Session) => `term:${s.fx.name}`;

export function newSession(fx: AgentFixture, since: Timestamp): Session {
  const open = fx.session !== null;
  return {
    fx,
    state: fx.state,
    since,
    sessionId: fx.session,
    open,
    hasTerminal: fx.state !== 'no_terminal',
    lines: open ? BANNER.map((l) => l.replace('{worktree}', fx.worktree)) : [],
    seq: 0,
    cols: COLS,
    rows: ROWS,
    buffer: '',
    composing: false,
    holder: DAEMON_HOLDER,
    prompt: null,
    queue: [],
    counter: 0,
    settings: defaultSettings(fx.model, fx.effort),
    received: [],
  };
}

/** The screen as `rows` lines, padded with blanks. */
export function visible(s: Session): string[] {
  const tail = s.lines.slice(-s.rows);
  return [...tail, ...Array(s.rows - tail.length).fill('')];
}
export const scrolledOff = (s: Session): number => Math.max(0, s.lines.length - s.rows);

export function view(s: Session): AgentView {
  return {
    name: s.fx.name,
    role: s.fx.role,
    model: s.fx.model,
    effort: s.fx.effort,
    state: s.state,
    state_since: s.since,
    state_detail: '',
    block: s.fx.wall ? { kind: 'wall', since: s.since } : null,
    wall_reason: s.fx.wall ? 'usage limit' : null,
    account: s.fx.account,
    task: s.fx.task,
    cache_until: s.open && s.state === 'idle' ? new Date(Date.parse(s.since) + 5 * 60_000).toISOString() : null,
    has_dialog: s.prompt !== null,
    needs_you: s.prompt
      ? { kind: s.prompt.kind === 'permission' || s.prompt.kind === 'question' ? s.prompt.kind : 'dialog', since: s.prompt.since, text: s.prompt.reason ?? s.prompt.command ?? null, source: 'hook' }
      : null,
    parent: s.fx.parent,
    ...(s.structured && { structured: true }),
    ...s.fx.view,
  };
}

export function detail(s: Session): AgentDetail {
  return {
    ...view(s),
    worktree: { path: s.fx.worktree, branch: s.fx.branch },
    cwd: s.fx.worktree,
    can: Object.fromEntries(
      ['terminal', 'wake', 'close', 'restart', 'park', 'fold', 'move'].map((k) => [k, { ok: true, why: null }]),
    ) as AgentDetail['can'],
    terminal: s.hasTerminal ? tid(s) : null,
    session_id: s.sessionId,
    session_open: s.open,
    input_holders: s.structured ? [] : [s.holder],
    account_pick: { account: s.fx.account, reason: 'fixture' },
    prompt: s.prompt,
    settings: s.settings as AgentDetail['settings'],
  };
}

/** accounts.list for a daemon given account names (each with the agents whose fixture names it); refused as unsupported without them. */
export function accountsReply(names: string[] | null, sessions: Session[]) {
  if (!names) throw refused('unsupported', 'the fake daemon does not serve accounts.list');
  const accounts = names.map((name) => ({
    name,
    label: name,
    login_state: 'ok' as const,
    five_hour: null,
    weekly: null,
    agents: sessions.filter((s) => s.fx.account === name).map((s) => s.fx.name),
    moves: [],
    cap: null,
    cap_effective: 3,
    recover: false,
  }));
  return { accounts };
}

export function statusReply(sessions: Map<string, Session>, unread: number, startedAt: Timestamp) {
  const by_state: Record<string, number> = {};
  for (const s of sessions.values()) by_state[s.state] = (by_state[s.state] ?? 0) + 1;
  return {
    alive: true,
    version: 'fake0000',
    protocol: PROTOCOL,
    schema: 1,
    started_at: startedAt,
    terminal_host: { name: 'fake', health: 'ok' },
    agents: { total: sessions.size, by_state },
    inbox: { unread },
  };
}
