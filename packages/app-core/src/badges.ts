import type { AgentDetail, AgentView } from '@handline/client';
import { STATE_WORDS } from '@handline/client';

export type BadgeKind = 'dialog' | 'stalled' | 'you' | 'held' | 'no_terminal' | 'no_session' | 'other';
export interface Badge {
  kind: BadgeKind;
  label: string;
  /** The tooltip: what the badge means and, for a stall, why and since when. */
  tip: string;
}

/**
 * The badges of a terminal tab and its sidebar row, from the daemon's parsed facts and never from the terminal's bytes.
 * `terminal` exists only on a detail: a null one means the agent has no terminal. A state word this client does not know
 * becomes a plain badge so it is still said.
 */
export function badgesOf(a: AgentView | AgentDetail, me: string): Badge[] {
  const out: Badge[] = [];
  const detail = a as Partial<AgentDetail>;
  if (a.has_dialog || detail.prompt) out.push({ kind: 'dialog', label: '!', tip: 'A dialog is open: answer it in Chat' });
  if (a.block || a.state === 'stalled' || a.state === 'blocked') {
    const why = a.block ? `${a.block.kind} since ${a.block.since}` : a.state;
    out.push({ kind: 'stalled', label: a.state === 'stalled' ? 'STALLED' : 'blocked', tip: why });
  }
  const holder = a.input_holders?.[0];
  if (holder && holder.kind !== 'daemon')
    out.push(
      holder.name === me
        ? { kind: 'you', label: 'YOU', tip: 'This device holds the input' }
        : {
            kind: 'held',
            label: `held: ${holder.name}`,
            tip: `${holder.name} holds the input${holder.since ? ` since ${holder.since}` : ''}`,
          },
    );
  if (a.structured) {
    if (detail.session_open === false) out.push({ kind: 'no_session', label: 'no session', tip: 'Start or resume the agent to open one' });
  } else if (a.state === 'no_terminal' || detail.terminal === null)
    out.push({ kind: 'no_terminal', label: 'no terminal', tip: 'Start or resume the agent to open one' });
  if (!(STATE_WORDS as readonly string[]).includes(a.state)) out.push({ kind: 'other', label: a.state, tip: `state: ${a.state}` });
  return out;
}

/** What makes a hidden terminal tab worth a dot: a dialog or a stall. */
export const needsAttention = (badges: Badge[]): boolean => badges.some((b) => b.kind === 'dialog' || b.kind === 'stalled');
