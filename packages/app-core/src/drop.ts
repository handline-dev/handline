// Where a dragged tab lands on a pane, and the rectangle the drop shows: the zone is chosen once per pane from the pointer, and the preview is
// the exact place the result takes (a split is always half), so what the person sees is what they get. Pure; the web draws it.
import type { Edge } from '#src/workspace.ts';

export type DropZone = { kind: 'strip' } | { kind: 'center' } | { kind: 'edge'; edge: Edge };
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const EDGE_SHARE = 0.3;
const EDGE_MIN = 40;
const EDGE_MAX = 160;
const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/**
 * The zone under the pointer `(x, y)`, measured from the pane's top left. `strip` is the height of the tab strip (the top of the pane); the body
 * below it is an edge when within 30% of a side (at least 40 px, at most 160 px), the closer side by share at a corner, else the centre.
 * `edges` false leaves only the strip and the centre (a narrow window shows one strip, so there is nowhere to split to).
 */
export function dropZone(pane: { w: number; h: number; strip: number }, at: { x: number; y: number }, edges = true): DropZone {
  if (at.y < pane.strip) return { kind: 'strip' };
  if (!edges) return { kind: 'center' };
  const body = pane.h - pane.strip;
  const y = at.y - pane.strip;
  const lx = clamp(EDGE_SHARE * pane.w, EDGE_MIN, EDGE_MAX);
  const ly = clamp(EDGE_SHARE * body, EDGE_MIN, EDGE_MAX);
  const near: [Edge, number][] = [];
  if (at.x < lx) near.push(['left', at.x / pane.w]);
  if (pane.w - at.x < lx) near.push(['right', (pane.w - at.x) / pane.w]);
  if (y < ly) near.push(['top', y / body]);
  if (body - y < ly) near.push(['bottom', (body - y) / body]);
  const best = near.sort((a, b) => a[1] - b[1])[0];
  return best ? { kind: 'edge', edge: best[0] } : { kind: 'center' };
}

/** The box a drop in `zone` fills, in the pane's own coordinates; null for the strip (it shows an insert line instead). */
export function dropPreview(pane: { w: number; h: number }, zone: DropZone): Box | null {
  if (zone.kind === 'strip') return null;
  if (zone.kind === 'center') return { x: 0, y: 0, w: pane.w, h: pane.h };
  const { w, h } = pane;
  return {
    left: { x: 0, y: 0, w: w / 2, h },
    right: { x: w / 2, y: 0, w: w / 2, h },
    top: { x: 0, y: 0, w, h: h / 2 },
    bottom: { x: 0, y: h / 2, w, h: h / 2 },
  }[zone.edge];
}
