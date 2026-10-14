// Marking up a picture, with no platform in it: the shapes, the tool state and undo. A view feeds it points in image pixels and draws
// `state.shapes` (plus `state.draft` while a stroke is down); `annotation-raster.ts` turns the same shapes into pixels.

export type AnnotationTool = 'pen' | 'arrow' | 'box' | 'text';
export interface Point { x: number; y: number }
interface ShapeBase { id: number; color: string; width: number }
export type Shape =
  | (ShapeBase & { kind: 'pen'; points: Point[] })
  | (ShapeBase & { kind: 'arrow'; from: Point; to: Point })
  | (ShapeBase & { kind: 'box'; from: Point; to: Point })
  | (ShapeBase & { kind: 'text'; at: Point; text: string; size: number });

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
  | { type: 'undo' | 'redo' | 'clear' };

export const ANNOTATION_COLORS = ['#e5322d', '#f5a623', '#2fa84f', '#2f6bff', '#111111', '#ffffff'] as const;

export const initialAnnotation = (): AnnotationState => ({
  shapes: [], redo: [], draft: null, tool: 'pen', color: ANNOTATION_COLORS[0], width: 6, next: 1,
});

/** The height of a text shape's capitals, in image pixels, for a line width. */
export const textSize = (width: number) => Math.max(14, width * 4);

function commit(s: AnnotationState, shape: Shape): AnnotationState {
  return { ...s, shapes: [...s.shapes, shape], redo: [], draft: null, next: s.next + 1 };
}

export function annotate(s: AnnotationState, a: AnnotationAction): AnnotationState {
  switch (a.type) {
    case 'tool': return s.draft ? s : { ...s, tool: a.tool };
    case 'color': return { ...s, color: a.color };
    case 'width': return { ...s, width: Math.max(1, a.width) };
    case 'strokeStart': {
      const at = { x: a.x, y: a.y };
      const base = { id: s.next, color: s.color, width: s.width };
      if (s.tool === 'text') return s;
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
    case 'strokeCancel': return { ...s, draft: null };
    case 'text': {
      const text = a.text.trim();
      if (!text) return s;
      return commit(s, { id: s.next, kind: 'text', color: s.color, width: s.width, at: { x: a.x, y: a.y }, text, size: textSize(s.width) });
    }
    case 'undo': {
      const last = s.shapes[s.shapes.length - 1];
      return last ? { ...s, shapes: s.shapes.slice(0, -1), redo: [last, ...s.redo], draft: null } : s;
    }
    case 'redo': {
      const [first, ...rest] = s.redo;
      return first ? { ...s, shapes: [...s.shapes, first], redo: rest } : s;
    }
    case 'clear': return { ...s, shapes: [], redo: [], draft: null };
  }
}
