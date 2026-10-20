// What every app's terminal shares: the resize debounce and the colours past the theme's 16. Pure.

/** How long a terminal waits after its last resize before it tells the daemon its new size. */
export const RESIZE_MS = 150;

const CUBE = [0, 95, 135, 175, 215, 255];
const hex = (n: number) => n.toString(16).padStart(2, '0');

/** Palette index 16-255 as `#rrggbb`: the xterm 6×6×6 cube, then 24 greys. Undefined for any other index; 0-15 are each app's theme. */
export function xtermColor(c: number): string | undefined {
  if (!Number.isInteger(c) || c < 16 || c > 255) return undefined;
  if (c < 232) {
    const i = c - 16;
    return `#${hex(CUBE[Math.floor(i / 36)]!)}${hex(CUBE[Math.floor(i / 6) % 6]!)}${hex(CUBE[i % 6]!)}`;
  }
  const g = 8 + (c - 232) * 10;
  return `#${hex(g)}${hex(g)}${hex(g)}`;
}
