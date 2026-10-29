// A state is a glyph and a word; the hue only repeats them, so it never carries a state alone. Working spins and starting pulses (both stop for reduced motion).
import { CircleCheck, CircleDashed, CirclePause, CircleSlash, Hand, Loader, Moon, Circle, type LucideIcon } from 'lucide-react';

export type StateName = 'working' | 'needs' | 'blocked' | 'idle' | 'waiting' | 'starting' | 'done' | 'dropped' | 'draft';

const SPEC: Record<StateName, { Glyph: LucideIcon; word: string; look: string }> = {
  working: { Glyph: Loader, word: 'Working', look: 'text-working animate-[spin_1.6s_linear_infinite]' },
  needs: { Glyph: Hand, word: 'Needs you', look: 'text-wait' },
  blocked: { Glyph: CirclePause, word: 'Blocked', look: 'text-bad' },
  idle: { Glyph: Circle, word: 'Idle', look: '' },
  waiting: { Glyph: Moon, word: 'Waiting for a time', look: '' },
  starting: { Glyph: CircleDashed, word: 'Starting', look: 'animate-pulse' },
  done: { Glyph: CircleCheck, word: 'Done', look: 'text-ok' },
  dropped: { Glyph: CircleSlash, word: 'Dropped', look: 'text-ink-3' },
  draft: { Glyph: CircleDashed, word: 'Draft', look: '' },
};

/** `word` prints the state's name beside the glyph; without it the glyph carries the name as its label. */
export function StateMark({ state, word, label }: { state: StateName; word?: boolean; label?: string }) {
  const { Glyph, word: name, look } = SPEC[state];
  return (
    <span className="inline-flex items-center gap-[5px] text-sm text-ink-2" data-state={state}>
      <Glyph
        width={15}
        height={15}
        strokeWidth={1.75}
        className={`motion-reduce:animate-none ${look}`}
        role={word ? undefined : 'img'}
        aria-label={word ? undefined : (label ?? name)}
        aria-hidden={word ? true : undefined}
      />
      {word ? (label ?? name) : null}
    </span>
  );
}
