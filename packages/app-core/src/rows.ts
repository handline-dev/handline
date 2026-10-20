// A styled terminal row as the pieces to draw, in order: the same layout for every app, so a run is drawn at its column however the text was
// trimmed. Columns count code points. A run may start or reach past the end of the text (blank cells with a background): the cells before it
// that no run covers are default blanks, and the run itself is padded to its end.
import type { Spans } from '@handline/client';

export type SpanStyle = Spans[number]['style'];
export interface Piece {
  text: string;
  /** Absent for a stretch no run covers. */
  style?: SpanStyle;
}

export function rowPieces(row: { text: string; spans?: Spans }): Piece[] {
  const chars = Array.from(row.text);
  const blanks = (from: number, to: number) =>
    chars.slice(from, to).join('') + ' '.repeat(Math.max(0, to - from - chars.slice(from, to).length));
  const out: Piece[] = [];
  let at = 0;
  for (const s of [...(row.spans ?? [])].sort((a, b) => a.from - b.from)) {
    const from = Math.max(s.from, at);
    if (s.to <= from) continue;
    if (from > at) out.push({ text: blanks(at, from) });
    out.push({ text: blanks(from, s.to), style: s.style });
    at = s.to;
  }
  if (at < chars.length) out.push({ text: chars.slice(at).join('') });
  return out;
}
