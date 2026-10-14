// What a task state looks like, as names: the glyph and the tone. The web maps a glyph name to its icon and a tone to a class;
// the phone maps them to its own marks. The words of a waiting reason live here too, so a list row and a graph edge say the same.

export const STATE_GLYPH = {
  draft: 'taskDraft',
  queued: 'taskQueued',
  pending: 'taskPending',
  doing: 'taskDoing',
  blocked: 'taskBlocked',
  done: 'taskDone',
  discarded: 'taskDiscarded',
} as const;

export type Tone = 'run' | 'wait' | 'block' | 'idle';

/** `run` is working, `wait` is waiting on someone or something, `block` is stuck, `idle` is nothing to watch. */
export const STATE_TONE: Record<string, Tone> = {
  doing: 'run',
  queued: 'wait',
  pending: 'wait',
  draft: 'idle',
  blocked: 'block',
  done: 'idle',
  discarded: 'idle',
};

export const toneOf = (state: string): Tone => STATE_TONE[state] ?? 'idle';
export const glyphOf = (state: string): (typeof STATE_GLYPH)[keyof typeof STATE_GLYPH] =>
  (STATE_GLYPH as Record<string, (typeof STATE_GLYPH)[keyof typeof STATE_GLYPH]>)[state] ?? 'taskPending';

/** The one vocabulary of waiting (docs/story1-research.md section 3). */
export const WAIT_WORDS = {
  founder: 'waiting on you',
  cap: 'at its cap',
  wall: 'wall',
} as const;
export const blockedBy = (ids: readonly number[]): string => `blocked by ${ids.map((n) => `T${n}`).join(', ')}`;
export const hostFull = (used: number, cap: number): string => `host full: ${used} of ${cap}`;
