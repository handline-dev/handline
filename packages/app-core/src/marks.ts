// Memo marks on a capture, with no platform in it: one gesture on the picture is one mark (a tap pins a circle, a drag draws a box, a loop
// marks an area), its memo opens at the mark, and what goes to the agent is the numbered lines plus each mark's shape and pixel rect.
// A view feeds points in image pixels; `annotation.ts` and `annotation-raster.ts` draw the shapes `marksToShapes` makes of the marks.
import { textSize, type Point, type Shape } from '#src/annotation.ts';

export type MarkKind = 'pin' | 'box' | 'area';
export interface MarkRect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface Mark {
  id: number;
  kind: MarkKind;
  /** The pixels the mark covers: a pin's circle, a box, an area's bounding box. */
  rect: MarkRect;
  /** An area's loop, as the pointer went round it. */
  points?: Point[];
  text: string;
}

export interface MarksState {
  marks: Mark[];
  /** The `marks` before each add or removal, newest first; Undo steps back through them. */
  past: Mark[][];
  /** The pointer's path while it is down. */
  draft: Point[] | null;
  /** The mark whose memo is open. */
  open: number | null;
  next: number;
  /** A gesture that stays inside this many pixels is a tap. */
  slop: number;
  /** The radius of a pin's circle. */
  radius: number;
}

export type MarksAction =
  | { type: 'down' | 'move' | 'up'; x: number; y: number }
  | { type: 'cancel' | 'undo' | 'clear' }
  | { type: 'memo'; id: number; text: string }
  | { type: 'open'; id: number | null }
  | { type: 'remove'; id: number }
  /** The picture's size sets the sizes of a tap and a pin. */
  | { type: 'scale'; slop: number; radius: number };

export const initialMarks = (): MarksState => ({ marks: [], past: [], draft: null, open: null, next: 1, slop: 8, radius: 16 });

const MAX_LOOP_POINTS = 64;

const rectOf = (ps: Point[]): MarkRect => {
  const xs = ps.map((p) => p.x),
    ys = ps.map((p) => p.y);
  const x = Math.min(...xs),
    y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
};
const round = (r: MarkRect): MarkRect => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) });

/** Every n-th point, the last kept: a loop of a thousand pointer events is a shape of at most MAX_LOOP_POINTS. */
const thin = (ps: Point[]): Point[] => {
  const n = Math.ceil(ps.length / MAX_LOOP_POINTS);
  return ps.filter((_, i) => i % n === 0 || i === ps.length - 1).map((p) => ({ x: Math.round(p.x), y: Math.round(p.y) }));
};

/** What a pointer path is: a tap (it stayed within `slop`), a loop (it came back near where it began after going round), or a drag from start to end. */
export function classifyGesture(path: Point[], slop: number, radius: number): Pick<Mark, 'kind' | 'rect' | 'points'> {
  const box = rectOf(path);
  const first = path[0]!,
    last = path.at(-1)!;
  if (Math.max(box.w, box.h) < slop) {
    return { kind: 'pin', rect: round({ x: first.x - radius, y: first.y - radius, w: radius * 2, h: radius * 2 }) };
  }
  const diag = Math.hypot(box.w, box.h);
  const length = path.slice(1).reduce((n, p, i) => n + Math.hypot(p.x - path[i]!.x, p.y - path[i]!.y), 0);
  if (Math.hypot(last.x - first.x, last.y - first.y) <= diag * 0.35 && length >= diag * 1.8) {
    return { kind: 'area', rect: round(box), points: thin(path) };
  }
  // A line is a box with some height, so a drag along an edge still marks a place.
  const drag = rectOf([first, last]);
  return { kind: 'box', rect: round({ ...drag, w: Math.max(drag.w, slop), h: Math.max(drag.h, slop) }) };
}

const commit = (s: MarksState, marks: Mark[], open: number | null): MarksState => ({ ...s, marks, past: [s.marks, ...s.past], open });

export function marksReducer(s: MarksState, a: MarksAction): MarksState {
  switch (a.type) {
    case 'scale':
      return { ...s, slop: a.slop, radius: a.radius };
    case 'down':
      return { ...s, draft: [{ x: a.x, y: a.y }] };
    case 'move':
      return s.draft ? { ...s, draft: [...s.draft, { x: a.x, y: a.y }] } : s;
    case 'up': {
      if (!s.draft) return s;
      const mark: Mark = { id: s.next, text: '', ...classifyGesture([...s.draft, { x: a.x, y: a.y }], s.slop, s.radius) };
      return { ...commit(s, [...s.marks, mark], mark.id), draft: null, next: s.next + 1 };
    }
    case 'cancel':
      return { ...s, draft: null };
    case 'clear':
      return { ...initialMarks(), slop: s.slop, radius: s.radius };
    case 'memo':
      return { ...s, marks: s.marks.map((m) => (m.id === a.id ? { ...m, text: a.text } : m)) };
    case 'open':
      return { ...s, open: a.id };
    case 'remove':
      return s.marks.some((m) => m.id === a.id) ? commit(s, s.marks.filter((m) => m.id !== a.id), s.open === a.id ? null : s.open) : s;
    case 'undo': {
      const [marks, ...past] = s.past;
      return marks ? { ...s, marks, past, draft: null, open: null } : { ...s, draft: null };
    }
  }
}

/** The marks that go out: those with words, in order. The numbers the picture and the lines show count these only. */
export const keptMarks = (marks: readonly Mark[]): Mark[] => marks.filter((m) => m.text.trim());

/** A mark as the agent reads it: its number, shape, pixel rect in the picture, and words. */
export interface MarkData extends MarkRect {
  n: number;
  shape: MarkKind;
  text: string;
}
export const markData = (marks: readonly Mark[]): MarkData[] =>
  keptMarks(marks).map((m, i) => ({ n: i + 1, shape: m.kind, ...m.rect, text: m.text.trim().replace(/\s*\n\s*/g, ' ') }));

/**
 * The words of the message: the person's note, one `n. words` line per kept mark, and the marks' shapes and rects as a `marks` block so a later
 * agent can crop. The pictures are named beside it by the sender (`withImages`).
 */
export function markText(note: string, marks: readonly Mark[]): string {
  const data = markData(marks);
  if (!data.length) return note.trim();
  const lines = data.map((d) => `${d.n}. ${d.text}`);
  return [note.trim(), ...lines, '```marks', JSON.stringify(data), '```'].filter(Boolean).join('\n');
}

export const MARK_COLOR = '#e5322d';

/**
 * The drawn shapes of the kept marks (all of them with `all`, for the screen while a memo is still empty), for `exportAnnotated` and `shapesToSvg`:
 * a ring, a box or the loop, each with its number at the top left.
 */
export function marksToShapes(marks: readonly Mark[], width: number, all = false): Shape[] {
  return (all ? marks : keptMarks(marks)).flatMap((m, i): Shape[] => {
    const base = { color: MARK_COLOR, width };
    const id = i * 2 + 1;
    const from = { x: m.rect.x, y: m.rect.y },
      to = { x: m.rect.x + m.rect.w, y: m.rect.y + m.rect.h };
    const outline: Shape =
      m.kind === 'pin'
        ? { ...base, id, kind: 'ellipse', from, to }
        : m.kind === 'box'
          ? { ...base, id, kind: 'box', from, to }
          : { ...base, id, kind: 'pen', points: [...(m.points ?? []), ...(m.points?.[0] ? [m.points[0]] : [])] };
    const size = textSize(width);
    return [outline, { ...base, id: id + 1, kind: 'text', at: { x: Math.max(0, from.x - size), y: Math.max(0, from.y - size) }, text: String(i + 1), size }];
  });
}
