// One glyph per state (docs/design-gaps.md §1.3): no screen draws its own dot. The glyph, the tooltip and the aria-label come from here, and the
// hue only repeats what the shape already says.
import { dotOf, type Dot } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { Icon, type IconName } from './Icon.tsx';

export type StateKind = 'working' | 'booting' | 'needs' | 'blocked' | 'idle' | 'parked' | 'done' | 'rejected' | 'noTerminal';

const GLYPH: Record<StateKind, { icon: IconName; label: string }> = {
  working: { icon: 'working', label: 'Working' },
  booting: { icon: 'booting', label: 'Starting' },
  needs: { icon: 'needs', label: 'Needs you' },
  blocked: { icon: 'blocked', label: 'Blocked' },
  idle: { icon: 'idle', label: 'Idle' },
  parked: { icon: 'waiting', label: 'Parked' },
  done: { icon: 'done', label: 'Done' },
  rejected: { icon: 'dismissed', label: 'Rejected' },
  noTerminal: { icon: 'noTerminal', label: 'No terminal' },
};

const WORDS: Record<string, StateKind> = {
  running: 'working',
  working: 'working',
  doing: 'working',
  booting: 'booting',
  waking: 'booting',
  starting: 'booting',
  queued: 'booting',
  waiting: 'needs',
  needs: 'needs',
  pending: 'idle',
  blocked: 'blocked',
  failed: 'blocked',
  stalled: 'blocked',
  idle: 'idle',
  parked: 'parked',
  stopped: 'idle',
  draft: 'idle',
  done: 'done',
  discarded: 'idle',
};

/** The daemon's state word (an agent's or a task's) as a state glyph; a word it does not know is idle. */
export const stateOf = (word: string): StateKind => WORDS[word.toLowerCase()] ?? 'idle';

const DOT: Record<Dot, StateKind> = {
  running: 'working',
  waking: 'booting',
  waiting: 'needs',
  blocked: 'blocked',
  none: 'noTerminal',
  parked: 'parked',
  idle: 'idle',
};

/** An agent's state from the whole view, not the word alone: a dialog open is "needs you", a block is "blocked", and no terminal says so. */
export const stateOfView = (a: AgentView): StateKind => DOT[dotOf(a)];

/** `label` replaces the tooltip when the screen knows why ("Blocked: waits for the build"); `word` also prints the name beside the glyph. */
export function State({ kind, label, word, size = 16 }: { kind: StateKind; label?: string; word?: boolean; size?: number }) {
  const g = GLYPH[kind];
  const text = label ?? g.label;
  return (
    <span className="ui-state" data-state={kind} title={text}>
      {kind === 'noTerminal' ? (
        <span className="ui-state-slash">
          <Icon name={g.icon} size={size} label={text} />
          <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
            <line x1="2" y1="14" x2="14" y2="2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </span>
      ) : (
        <Icon name={g.icon} size={size} label={text} />
      )}
      {word ? <span className="ui-state-word">{g.label}</span> : null}
    </span>
  );
}
