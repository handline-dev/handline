// The phone terminal: the grid that fits the screen, and who has to be asked before the host's size is changed.
import type { InputHolder } from '@handline/client';

/** A monospace cell is this wide and this tall, as a share of its font size. */
const CELL = { w: 0.6, h: 1.2 };
const MAX_FONT = 11;
const MIN_FONT = 6;

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** The font that fits this many columns into the width: the phone's own size when they fit, smaller when the host's grid is wider. */
export const fontFor = (width: number, cols: number) => clamp(Math.floor((width / (cols * CELL.w)) * 10) / 10, MIN_FONT, MAX_FONT);

/** The grid asked of the host for a pane of this size in pixels. The daemon never resizes under 80 by 24, so a narrow screen gets 80 columns in a smaller font. */
export function fitGrid(width: number, height: number): { cols: number; rows: number } {
  const cols = clamp(Math.floor(width / (MAX_FONT * CELL.w)), 80, 400);
  return { cols, rows: clamp(Math.floor(height / (fontFor(width, cols) * CELL.h)), 24, 200) };
}

const person = (h: InputHolder | null | undefined): h is InputHolder => !!h && h.kind !== 'daemon';

/** Another device holds the input. */
export const heldByOther = (holder: InputHolder | null | undefined, me: string) => person(holder) && holder.name !== me;

/** This device holds the input. */
export const heldByMe = (holder: InputHolder | null | undefined, me: string) => person(holder) && holder.name === me;

/** The host's grid is wider than the screen: its lines would wrap or be cut off here. */
export const sizeBreaks = (hostCols: number, fit: { cols: number }) => hostCols > fit.cols;
