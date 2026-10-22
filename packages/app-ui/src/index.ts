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
const STATE_TONE: Record<string, Tone> = {
  doing: 'run',
  queued: 'wait',
  pending: 'wait',
  draft: 'idle',
  blocked: 'block',
  done: 'idle',
  discarded: 'idle',
};

export const toneOf = (state: string): Tone => STATE_TONE[state] ?? 'idle';
/** The one vocabulary of waiting (docs/story1-research.md section 3). */
export const WAIT_WORDS = {
  founder: 'waiting on you',
} as const;
export const blockedBy = (ids: readonly number[]): string => `blocked by ${ids.map((n) => `T${n}`).join(', ')}`;
export * from '#src/icons.ts';

/** Where a popover opens next to its anchor: the side with room, and the edge it lines up with. Both apps' popovers and menus place by this. */
export type Box = { top: number; bottom: number; left: number; right: number };
export type Placement = { side: 'top' | 'bottom'; align: 'start' | 'end'; maxHeight: number };

/** `prefer` is kept while the panel fits there; else the other side is taken when it has more room. A panel wider than the room on the
 * `start` side lines up with the anchor's `end`, and the reverse. maxHeight is what the chosen side leaves (never below 0). */
export function placePopover(
  anchor: Box,
  panel: { w: number; h: number },
  view: { w: number; h: number },
  prefer: { side?: 'top' | 'bottom'; align?: 'start' | 'end' } = {},
  gap = 4,
  pad = 8,
): Placement {
  const below = view.h - anchor.bottom - gap - pad;
  const above = anchor.top - gap - pad;
  const first = prefer.side ?? 'bottom';
  const room = { bottom: below, top: above };
  const other = first === 'bottom' ? 'top' : 'bottom';
  const side = room[first] >= panel.h || room[first] >= room[other] ? first : other;
  const wantEnd = prefer.align === 'end';
  const fitsStart = anchor.left + panel.w <= view.w - pad;
  const fitsEnd = anchor.right - panel.w >= pad;
  const align = wantEnd ? (fitsEnd || !fitsStart ? 'end' : 'start') : fitsStart || !fitsEnd ? 'start' : 'end';
  return { side, align, maxHeight: Math.max(0, room[side]) };
}
