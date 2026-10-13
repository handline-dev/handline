// What the debug window says about one pane of debug.terminals: its fields as text ("-" for unknown) and the flags that point at a
// pane worth closing (no agent has it, the agent's CLI has exited, its prompt cache has lapsed).
import type { Methods } from '@handline/client';

export type DebugPane = Methods['debug.terminals']['result']['panes'][number];

export const DEBUG_HASH = '#/debug/terminals';

/** A value as the metadata block shows it: "-" when the daemon does not know it. */
export const dash = (v: unknown): string => (v === null || v === undefined || v === '' ? '-' : String(v));

/** Seconds as a short span: 45s, 12m, 3h 12m, 2d 4h. */
export function span(s: number): string {
  const n = Math.max(0, Math.round(s));
  if (n < 60) return `${n}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m`;
  if (n < 86400) return `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
  return `${Math.floor(n / 86400)}d ${Math.floor((n % 86400) / 3600)}h`;
}

/** Seconds until the pane's agent's prompt cache lapses (negative once it has); null when the daemon keeps no clock for it. */
export function cacheLeft(p: DebugPane, nowMs: number): number | null {
  if (!p.cache_until) return null;
  const at = Date.parse(p.cache_until);
  return Number.isNaN(at) ? null : Math.round((at - nowMs) / 1000);
}

export type Flag = { word: string; why: string; bad: boolean };

/** The flags of a pane, worst first. */
export function flags(p: DebugPane, nowMs: number): Flag[] {
  const out: Flag[] = [];
  if (p.orphan) {
    const was = p.recorded_agent ? ` (opened for ${p.recorded_agent})` : '';
    out.push({ word: 'orphan', why: `No agent has this pane as its terminal${was}`, bad: true });
  }
  if (p.process === 'shell' && (p.agent || p.recorded_agent))
    out.push({ word: 'agent exited', why: 'No agent CLI runs in the pane: only a shell is left', bad: true });
  const left = cacheLeft(p, nowMs);
  if (left !== null && left < 0 && p.process === 'agent')
    out.push({ word: 'cache expired', why: `The prompt cache lapsed ${span(-left)} ago and the session is still open`, bad: true });
  if (p.closed) out.push({ word: 'closed', why: 'The agent is closed but its pane still stands', bad: true });
  if (p.dead) out.push({ word: 'dead', why: 'The pane’s process has exited', bad: true });
  if (p.process === 'agent') out.push({ word: 'agent running', why: `${p.agent_process?.name ?? 'An agent CLI'} runs in the pane`, bad: false });
  return out;
}

/** The rows of the metadata block, label then value. */
export function facts(p: DebugPane, nowMs: number): [string, string][] {
  const left = cacheLeft(p, nowMs);
  const process =
    p.process === 'agent' && p.agent_process ? `${p.agent_process.name} (pid ${p.agent_process.pid})` : p.process === 'agent' ? 'agent' : p.process;
  return [
    ['Agent', p.agent ?? (p.recorded_agent ? `none (opened for ${p.recorded_agent})` : '-')],
    ['State', dash(p.state)],
    ['Account', dash(p.account)],
    ['Session id', dash(p.session_id)],
    ['Worktree', dash(p.worktree)],
    ['Pane path', dash(p.path)],
    ['Process', `${process} · pane pid ${dash(p.pid)} · ${dash(p.command)}`],
    ['Terminal', `${p.terminal} · ${p.session}:${p.window} ${p.window_name}`.trim()],
    ['Orca terminal', dash(p.orca_terminal)],
    ['Last activity', p.last_activity ? `${p.last_activity}${p.idle_s !== null ? ` (${span(p.idle_s)} idle)` : ''}` : '-'],
    ['Cache expiry', left === null ? '-' : `${p.cache_until} (${left < 0 ? `lapsed ${span(-left)} ago` : `in ${span(left)}`})`],
  ];
}
