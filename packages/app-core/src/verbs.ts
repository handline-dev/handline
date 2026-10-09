// The agent verbs of the bridge's AGENT_KEYS (tui.py): which apply to a row, which ask first, and what each is called while it runs.
import type { AgentDetail, AgentView } from '@handline/client';

export type VerbId = 'wake' | 'interrupt' | 'close' | 'restart' | 'restart_fresh' | 'park' | 'fold' | 'move' | 'clear_stall';

export interface Verb {
  id: VerbId;
  label: string;
  /** The daemon method behind it; a verb whose method the daemon does not list shows disabled. */
  method:
    | 'session.wake'
    | 'session.interrupt'
    | 'session.stop'
    | 'session.restart'
    | 'session.park'
    | 'agent.fold'
    | 'agent.move'
    | 'agent.unstall';
  /** Shown while the write is in flight, in the state cell. */
  gerund: string;
  /** One sentence of consequence for the confirmation; absent when the verb asks nothing. */
  ask?: string;
}

const V = (id: VerbId, label: string, method: Verb['method'], gerund: string, ask?: string): [VerbId, Verb] => [
  id,
  { id, label, method, gerund, ask },
];

export const VERBS: Record<VerbId, Verb> = Object.fromEntries([
  V('wake', 'Wake', 'session.wake', 'waking…'),
  V('interrupt', 'Interrupt', 'session.interrupt', 'interrupting…'),
  V('close', 'Close', 'session.stop', 'closing…', 'Its session ends; the terminal, worktree and account stay.'),
  V('restart', 'Restart', 'session.restart', 'restarting…', 'Its session ends and starts again, resuming the conversation.'),
  V(
    'restart_fresh',
    'Restart fresh',
    'session.restart',
    'restarting…',
    "Its session ends and starts again; the session's context is dropped.",
  ),
  V('park', 'Park', 'session.park', 'parking…', 'Its session ends; it wakes on the next task.'),
  V('fold', 'Fold into handoff', 'agent.fold', 'folding…', 'Its session is folded into the handoff it will start the next one from.'),
  V('move', 'Move account', 'agent.move', 'moving…'),
  V('clear_stall', 'Clear stall', 'agent.unstall', 'waking…', 'The stall is cleared and the agent is woken.'),
] as [VerbId, Verb][]) as Record<VerbId, Verb>;

/** The bridge's CONFIRM (close, restart, park, fold) with restart fresh as a restart, and wake only on a stalled row (clear stall). */
export const CONFIRM_VERBS: ReadonlySet<VerbId> = new Set(['close', 'restart', 'restart_fresh', 'park', 'fold', 'clear_stall']);
export const needsAsk = (id: VerbId): boolean => CONFIRM_VERBS.has(id);

/** The verbs that apply to this row now, in menu order. A verb that does not apply is absent, not greyed. */
export function verbsOf(a: AgentView, d: Pick<AgentDetail, 'session_open' | 'session_id' | 'structured'>): Verb[] {
  const ids: VerbId[] = [];
  // Interrupt ends a running turn, so it is listed for a structured agent only while one runs (a terminal agent has it in the stop menu).
  if (d.structured && d.session_open && (a.state === 'running' || a.state === 'waiting')) ids.push('interrupt');
  const stalled = a.state === 'stalled';
  if (stalled) ids.push('clear_stall');
  else if (!d.session_open && d.session_id) ids.push('wake');
  if (d.session_open) ids.push('restart', 'restart_fresh', 'close', 'park', 'fold');
  ids.push('move');
  return ids.map((id) => VERBS[id]);
}

/** Whether a verb asks first on this row: the confirm set. */
export const asks = (v: Verb): boolean => needsAsk(v.id);

/** The call a verb makes. */
export function callOf(v: Verb, agent: string, account?: string): { method: Verb['method']; params: Record<string, unknown> } {
  const params: Record<string, unknown> = { agent };
  if (v.id === 'close') params.mode = 'close';
  if (v.id === 'restart_fresh') params.fresh = true;
  if (v.id === 'move') params.account = account;
  return { method: v.method, params };
}
