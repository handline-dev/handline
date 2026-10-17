// Marking up a picture, with no platform in it: the shapes, the tool state and undo. A view feeds it points in image pixels and draws
// `state.shapes` (plus `state.draft` while a stroke is down); `annotation-raster.ts` turns the same shapes into pixels.

export type AnnotationTool = 'pen' | 'arrow' | 'box' | 'ellipse' | 'text' | 'note';
export interface Point {
  x: number;
  y: number;
}
interface ShapeBase {
  id: number;
  color: string;
  width: number;
}
export type Shape =
  | (ShapeBase & { kind: 'pen'; points: Point[] })
  | (ShapeBase & { kind: 'arrow'; from: Point; to: Point })
  | (ShapeBase & { kind: 'box'; from: Point; to: Point })
  | (ShapeBase & { kind: 'ellipse'; from: Point; to: Point })
  | (ShapeBase & { kind: 'text'; at: Point; text: string; size: number })
  /** A pinned note: a spot (`from` = `to`) or a region. The picture shows its number; the words are listed beside it, not drawn. */
  | (ShapeBase & { kind: 'note'; from: Point; to: Point; text: string });

export interface AnnotationState {
  shapes: Shape[];
  /** Undone shapes, newest first; any new shape empties it. */
  redo: Shape[];
  /** The shape under the finger, not yet in `shapes`. */
  draft: Shape | null;
  tool: AnnotationTool;
  color: string;
  width: number;
  next: number;
}

export type AnnotationAction =
  | { type: 'tool'; tool: AnnotationTool }
  | { type: 'color'; color: string }
  | { type: 'width'; width: number }
  | { type: 'strokeStart' | 'strokeMove' | 'strokeEnd'; x: number; y: number }
  | { type: 'strokeCancel' }
  | { type: 'text'; x: number; y: number; text: string }
  | { type: 'note'; from: Point; to: Point; text: string }
  /** New words for a note; none removes it. */
  | { type: 'noteText'; id: number; text: string }
  | { type: 'undo' | 'redo' | 'clear' };

export const ANNOTATION_COLORS = ['#e5322d', '#f5a623', '#2fa84f', '#2f6bff', '#111111', '#ffffff'] as const;

export const initialAnnotation = (): AnnotationState => ({
  shapes: [],
  redo: [],
  draft: null,
  tool: 'pen',
  color: ANNOTATION_COLORS[0],
  width: 6,
  next: 1,
});

/** The height of a text shape's capitals, in image pixels, for a line width. */
export const textSize = (width: number) => Math.max(14, width * 4);

function commit(s: AnnotationState, shape: Shape): AnnotationState {
  return { ...s, shapes: [...s.shapes, shape], redo: [], draft: null, next: s.next + 1 };
}

export function annotate(s: AnnotationState, a: AnnotationAction): AnnotationState {
  switch (a.type) {
    case 'tool':
      return s.draft ? s : { ...s, tool: a.tool };
    case 'color':
      return { ...s, color: a.color };
    case 'width':
      return { ...s, width: Math.max(1, a.width) };
    case 'strokeStart': {
      const at = { x: a.x, y: a.y };
      const base = { id: s.next, color: s.color, width: s.width };
      if (s.tool === 'text' || s.tool === 'note') return s; // their words are asked for first: the view adds them with its own action
      const draft: Shape = s.tool === 'pen' ? { ...base, kind: 'pen', points: [at] } : { ...base, kind: s.tool, from: at, to: at };
      return { ...s, draft };
    }
    case 'strokeMove': {
      const d = s.draft;
      if (!d || d.kind === 'text') return s;
      const at = { x: a.x, y: a.y };
      return { ...s, draft: d.kind === 'pen' ? { ...d, points: [...d.points, at] } : { ...d, to: at } };
    }
    case 'strokeEnd': {
      const moved = annotate(s, { type: 'strokeMove', x: a.x, y: a.y });
      const d = moved.draft;
      if (!d) return s;
      // A tap with no drag leaves nothing worth keeping, except a dot with the pen.
      const empty = d.kind !== 'pen' && d.kind !== 'text' && d.from.x === d.to.x && d.from.y === d.to.y;
      return empty ? { ...moved, draft: null } : commit(moved, d);
    }
    case 'strokeCancel':
      return { ...s, draft: null };
    case 'text': {
      const text = a.text.trim();
      if (!text) return s;
      return commit(s, { id: s.next, kind: 'text', color: s.color, width: s.width, at: { x: a.x, y: a.y }, text, size: textSize(s.width) });
    }
    case 'note': {
      const text = a.text.trim();
      return text ? commit(s, { id: s.next, kind: 'note', color: s.color, width: s.width, from: a.from, to: a.to, text }) : s;
    }
    case 'noteText': {
      const text = a.text.trim();
      const shapes = s.shapes.flatMap((x) => (x.id !== a.id || x.kind !== 'note' ? [x] : text ? [{ ...x, text }] : []));
      return { ...s, shapes, redo: [] };
    }
    case 'undo': {
      const last = s.shapes[s.shapes.length - 1];
      return last ? { ...s, shapes: s.shapes.slice(0, -1), redo: [last, ...s.redo], draft: null } : s;
    }
    case 'redo': {
      const [first, ...rest] = s.redo;
      return first ? { ...s, shapes: [...s.shapes, first], redo: rest } : s;
    }
    case 'clear':
      return { ...s, shapes: [], redo: [], draft: null };
  }
}

const pt = (p: Point, k: number): Point => ({ x: p.x * k, y: p.y * k });

/** The shape with every coordinate, width and text size multiplied by `k`. */
export function scaleShape(s: Shape, k: number): Shape {
  if (k === 1) return s;
  const width = s.width * k;
  switch (s.kind) {
    case 'pen':
      return { ...s, width, points: s.points.map((p) => pt(p, k)) };
    case 'text':
      return { ...s, width, at: pt(s.at, k), size: s.size * k };
    default:
      return { ...s, width, from: pt(s.from, k), to: pt(s.to, k) };
  }
}

/** The outline of the ellipse inscribed in the box `from`..`to`, as a closed polyline (first point repeated at the end). */
export function ellipsePoints(from: Point, to: Point, steps = 48): Point[] {
  const cx = (from.x + to.x) / 2,
    cy = (from.y + to.y) / 2,
    rx = Math.abs(to.x - from.x) / 2,
    ry = Math.abs(to.y - from.y) / 2;
  return Array.from({ length: steps + 1 }, (_, i) => ({
    x: cx + rx * Math.cos((2 * Math.PI * i) / steps),
    y: cy + ry * Math.sin((2 * Math.PI * i) / steps),
  }));
}

/** The two barb end points of an arrow's head; empty for a zero-length arrow. */
export function arrowBarbs(from: Point, to: Point, width: number): Point[] {
  const len = Math.hypot(to.x - from.x, to.y - from.y);
  if (len === 0) return [];
  const head = Math.min(len, Math.max(width * 4, 16));
  const back = Math.atan2(from.y - to.y, from.x - to.x);
  return [0.5, -0.5].map((turn) => ({ x: to.x + head * Math.cos(back + turn), y: to.y + head * Math.sin(back + turn) }));
}

/** The notes among `shapes`, in order: the number a picture shows is the place in this list plus one. */
export const notesOf = (shapes: readonly Shape[]) => shapes.filter((s): s is Extract<Shape, { kind: 'note' }> => s.kind === 'note');

/** What a note draws: a closed outline (the region, or a ring round a spot) and where its number goes, as the text shapes' top left. */
export function noteMark(s: Extract<Shape, { kind: 'note' }>): { outline: Point[]; label: Point; size: number } {
  const size = textSize(s.width);
  const x0 = Math.min(s.from.x, s.to.x),
    y0 = Math.min(s.from.y, s.to.y),
    x1 = Math.max(s.from.x, s.to.x),
    y1 = Math.max(s.from.y, s.to.y);
  if (x1 - x0 < 2 && y1 - y0 < 2) {
    const r = Math.max(10, s.width * 2.5);
    const at = { x: (x0 + x1) / 2, y: (y0 + y1) / 2 };
    return {
      outline: ellipsePoints({ x: at.x - r, y: at.y - r }, { x: at.x + r, y: at.y + r }),
      label: { x: at.x + r + s.width, y: at.y - size / 2 },
      size,
    };
  }
  return {
    outline: [
      { x: x0, y: y0 },
      { x: x1, y: y0 },
      { x: x1, y: y1 },
      { x: x0, y: y1 },
      { x: x0, y: y0 },
    ],
    label: { x: x0 + s.width * 1.5, y: y0 + s.width * 1.5 },
    size,
  };
}

export type SvgElement =
  | { type: 'path'; d: string; stroke: string; strokeWidth: number }
  | { type: 'text'; x: number; y: number; fontSize: number; fill: string; text: string };

const CAP_HEIGHT = 0.7;
const num = (n: number) => String(Math.round(n * 100) / 100);
const poly = (ps: Point[]) => ps.map((p, i) => `${i ? 'L' : 'M'}${num(p.x)} ${num(p.y)}`).join('');

/**
 * What a preview draws for `shapes`: paths to stroke with round caps and joins and no fill, and text anchored at its top left.
 * `width`/`height` are the image size, for the viewBox. Render each element with your SVG (react-native-svg or DOM).
 */
export function shapesToSvg(shapes: readonly Shape[], width: number, height: number): { viewBox: string; elements: SvgElement[] } {
  let n = 0;
  const elements: SvgElement[] = shapes.flatMap((s): SvgElement[] => {
    if (s.kind === 'note') {
      const m = noteMark(s);
      return [
        { type: 'path', d: poly(m.outline), stroke: s.color, strokeWidth: s.width },
        { type: 'text', x: m.label.x, y: m.label.y, fontSize: Number(num(m.size / CAP_HEIGHT)), fill: s.color, text: String(++n) },
      ];
    }
    // The raster's size is the capital height; a font's capitals are about 0.7 of its size, and the raster draws capitals only.
    if (s.kind === 'text')
      return [
        { type: 'text', x: s.at.x, y: s.at.y, fontSize: Number(num(s.size / CAP_HEIGHT)), fill: s.color, text: s.text.toUpperCase() },
      ];
    let d: string;
    if (s.kind === 'pen') d = s.points.length === 1 ? poly([s.points[0]!, s.points[0]!]) : poly(s.points);
    else if (s.kind === 'ellipse') d = `${poly(ellipsePoints(s.from, s.to))}Z`;
    else if (s.kind === 'box') d = `${poly([s.from, { x: s.to.x, y: s.from.y }, s.to, { x: s.from.x, y: s.to.y }])}Z`;
    else
      d =
        poly([s.from, s.to]) +
        arrowBarbs(s.from, s.to, s.width)
          .map((b) => `M${num(s.to.x)} ${num(s.to.y)}L${num(b.x)} ${num(b.y)}`)
          .join('');
    return [{ type: 'path', d, stroke: s.color, strokeWidth: s.width }];
  });
  return { viewBox: `0 0 ${num(width)} ${num(height)}`, elements };
}
