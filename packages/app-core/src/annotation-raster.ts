// Draws annotation shapes onto an RGBA bitmap in plain arithmetic, so the web and the phone produce the same pixels from the same shapes.
// Lines are round-capped discs, boxes are outlines, an arrow is a line with two barbs, text is a built-in 5x7 capital font.

import { arrowBarbs, scaleShape, type Point, type Shape } from './annotation.ts';

export interface Bitmap { width: number; height: number; /** RGBA, row by row. */ data: Uint8ClampedArray }

const GLYPHS: Record<string, string> = {
  A: '01110 10001 10001 11111 10001 10001 10001', B: '11110 10001 10001 11110 10001 10001 11110',
  C: '01110 10001 10000 10000 10000 10001 01110', D: '11110 10001 10001 10001 10001 10001 11110',
  E: '11111 10000 10000 11110 10000 10000 11111', F: '11111 10000 10000 11110 10000 10000 10000',
  G: '01110 10001 10000 10111 10001 10001 01111', H: '10001 10001 10001 11111 10001 10001 10001',
  I: '01110 00100 00100 00100 00100 00100 01110', J: '00111 00010 00010 00010 00010 10010 01100',
  K: '10001 10010 10100 11000 10100 10010 10001', L: '10000 10000 10000 10000 10000 10000 11111',
  M: '10001 11011 10101 10101 10001 10001 10001', N: '10001 11001 10101 10011 10001 10001 10001',
  O: '01110 10001 10001 10001 10001 10001 01110', P: '11110 10001 10001 11110 10000 10000 10000',
  Q: '01110 10001 10001 10001 10101 10010 01101', R: '11110 10001 10001 11110 10100 10010 10001',
  S: '01111 10000 10000 01110 00001 00001 11110', T: '11111 00100 00100 00100 00100 00100 00100',
  U: '10001 10001 10001 10001 10001 10001 01110', V: '10001 10001 10001 10001 10001 01010 00100',
  W: '10001 10001 10001 10101 10101 11011 10001', X: '10001 10001 01010 00100 01010 10001 10001',
  Y: '10001 10001 01010 00100 00100 00100 00100', Z: '11111 00001 00010 00100 01000 10000 11111',
  0: '01110 10001 10011 10101 11001 10001 01110', 1: '00100 01100 00100 00100 00100 00100 01110',
  2: '01110 10001 00001 00010 00100 01000 11111', 3: '11110 00001 00001 01110 00001 00001 11110',
  4: '00010 00110 01010 10010 11111 00010 00010', 5: '11111 10000 11110 00001 00001 10001 01110',
  6: '00110 01000 10000 11110 10001 10001 01110', 7: '11111 00001 00010 00100 01000 01000 01000',
  8: '01110 10001 10001 01110 10001 10001 01110', 9: '01110 10001 10001 01111 00001 00010 01100',
  '.': '00000 00000 00000 00000 00000 01100 01100', ',': '00000 00000 00000 00000 01100 00100 01000',
  '!': '00100 00100 00100 00100 00100 00000 00100', '?': '01110 10001 00001 00010 00100 00000 00100',
  ':': '00000 01100 01100 00000 01100 01100 00000', '-': '00000 00000 00000 11111 00000 00000 00000',
  '+': '00000 00100 00100 11111 00100 00100 00000', '/': '00001 00001 00010 00100 01000 10000 10000',
  '(': '00010 00100 01000 01000 01000 00100 00010', ')': '01000 00100 00010 00010 00010 00100 01000',
  '#': '01010 01010 11111 01010 11111 01010 01010', '_': '00000 00000 00000 00000 00000 00000 11111',
  '*': '00000 10101 01110 11111 01110 10101 00000', '=': '00000 00000 11111 00000 11111 00000 00000',
};
const UNKNOWN = '11111 10001 10001 10001 10001 10001 11111';
const GLYPH_W = 5;
const GLYPH_H = 7;

export function parseColor(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  const n = m ? parseInt(m[1]!, 16) : 0;
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export const blankBitmap = (width: number, height: number): Bitmap => ({ width, height, data: new Uint8ClampedArray(width * height * 4) });

function disc(b: Bitmap, cx: number, cy: number, r: number, rgb: [number, number, number]) {
  const x0 = Math.max(0, Math.floor(cx - r));
  const x1 = Math.min(b.width - 1, Math.ceil(cx + r));
  const y0 = Math.max(0, Math.floor(cy - r));
  const y1 = Math.min(b.height - 1, Math.ceil(cy + r));
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      if ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 > r * r) continue;
      const i = (y * b.width + x) * 4;
      b.data[i] = rgb[0]; b.data[i + 1] = rgb[1]; b.data[i + 2] = rgb[2]; b.data[i + 3] = 255;
    }
  }
}

function line(b: Bitmap, p: Point, q: Point, width: number, rgb: [number, number, number]) {
  const r = Math.max(0.5, width / 2);
  if (![p.x, p.y, q.x, q.y].every(Number.isFinite)) return;
  // Only the part of the segment that can touch the bitmap is walked, so a stroke of 1e9 px costs what its visible part does.
  let t0 = 0;
  let t1 = 1;
  const dx = q.x - p.x;
  const dy = q.y - p.y;
  const edges: [number, number][] = [[-dx, p.x + r], [dx, b.width + r - p.x], [-dy, p.y + r], [dy, b.height + r - p.y]];
  for (const [d, room] of edges) {
    if (d === 0) {
      if (room < 0) return;
    } else {
      const t = room / d;
      if (d < 0) t0 = Math.max(t0, t);
      else t1 = Math.min(t1, t);
    }
  }
  if (t0 > t1) return;
  const len = Math.hypot(dx, dy) * (t1 - t0);
  const steps = Math.max(1, Math.ceil(len / Math.max(0.5, r / 2)));
  for (let i = 0; i <= steps; i++) {
    const t = t0 + ((t1 - t0) * i) / steps;
    disc(b, p.x + dx * t, p.y + dy * t, r, rgb);
  }
}

function arrow(b: Bitmap, from: Point, to: Point, width: number, rgb: [number, number, number]) {
  line(b, from, to, width, rgb);
  for (const barb of arrowBarbs(from, to, width)) line(b, to, barb, width, rgb);
}

function text(b: Bitmap, at: Point, str: string, size: number, rgb: [number, number, number]) {
  const cell = size / GLYPH_H;
  let x = at.x;
  for (const ch of str.toUpperCase()) {
    if (ch !== ' ') {
      const rows = (GLYPHS[ch] || UNKNOWN).split(' ');
      rows.forEach((row, ry) => {
        for (let rx = 0; rx < GLYPH_W; rx++) {
          if (row[rx] !== '1') continue;
          const x0 = Math.round(x + rx * cell);
          const y0 = Math.round(at.y + ry * cell);
          const w = Math.max(1, Math.round(x + (rx + 1) * cell) - x0);
          const h = Math.max(1, Math.round(at.y + (ry + 1) * cell) - y0);
          for (let yy = Math.max(0, y0); yy < Math.min(b.height, y0 + h); yy++) {
            for (let xx = Math.max(0, x0); xx < Math.min(b.width, x0 + w); xx++) {
              const i = (yy * b.width + xx) * 4;
              b.data[i] = rgb[0]; b.data[i + 1] = rgb[1]; b.data[i + 2] = rgb[2]; b.data[i + 3] = 255;
            }
          }
        }
      });
    }
    x += (GLYPH_W + 1) * cell;
  }
}

/**
 * Paint `shapes` in order onto `bitmap` (modified in place; pass a copy of the source to keep it). Shapes are in original-image pixels;
 * `scale` is the bitmap's size over the original's (0.5 for a bitmap shrunk to half), applied to positions, line widths and text.
 */
export function drawShapes(bitmap: Bitmap, shapes: readonly Shape[], scale = 1): Bitmap {
  for (const shape of shapes) {
    const s = scaleShape(shape, scale);
    const rgb = parseColor(s.color);
    if (s.kind === 'pen') {
      const [first, ...rest] = s.points;
      if (!first) continue;
      if (rest.length === 0) disc(bitmap, first.x, first.y, Math.max(0.5, s.width / 2), rgb);
      let prev = first;
      for (const p of rest) { line(bitmap, prev, p, s.width, rgb); prev = p; }
    } else if (s.kind === 'arrow') {
      arrow(bitmap, s.from, s.to, s.width, rgb);
    } else if (s.kind === 'box') {
      const { from: f, to: t } = s;
      const corners = [f, { x: t.x, y: f.y }, t, { x: f.x, y: t.y }, f];
      for (let i = 0; i < 4; i++) line(bitmap, corners[i]!, corners[i + 1]!, s.width, rgb);
    } else {
      text(bitmap, s.at, s.text, s.size, rgb);
    }
  }
  return bitmap;
}

/** The source bitmap with the shapes drawn on it; the source is left alone. */
export function exportAnnotated(source: Bitmap, shapes: readonly Shape[], scale = 1): Bitmap {
  return drawShapes({ ...source, data: new Uint8ClampedArray(source.data) }, shapes, scale);
}
