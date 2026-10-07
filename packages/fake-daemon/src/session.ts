import type { AgentDetail, AgentView, InputHolder, Prompt, Row, Timestamp } from '@handline/contract';
import { BANNER, type AgentFixture } from './fixtures.ts';
import { refused } from './errors.ts';

export const ROWS = 24,
  COLS = 80;
export const DAEMON_HOLDER: InputHolder = { kind: 'daemon', name: 'daemon', since: null };
export const row = (text: string): Row => ({ text });

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
  queue: (() => void)[]; // the scripted turn, one step per tick
  counter: number;
  received: { kind: 'text' | 'key'; value: string; actor: string }[]; // what reached the terminal
}

/** How one connection watches a terminal: the format it asked for, and its own notice counter when that is bytes. */
export interface Attach {
  format: 'text' | 'bytes';
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
    parent: s.fx.parent,
  };
}
export function detail(s: Session): AgentDetail {
  return {
    ...view(s),
    worktree: { path: s.fx.worktree, branch: s.fx.branch },
    terminal: s.hasTerminal ? tid(s) : null,
    session_id: s.sessionId,
    session_open: s.open,
    input_holders: [s.holder],
    account_pick: { account: s.fx.account, reason: 'fixture' },
    prompt: s.prompt,
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
