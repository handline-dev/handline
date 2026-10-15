// What an app does with a picture before it is uploaded and after it is chosen, with no platform in it: the size to shrink to, and the line a
// text carries for an image already uploaded.

/** The longest side an app uploads; a larger picture is shrunk to it first. */
export const IMAGE_LONG_EDGE = 2048;

/** The size a picture shrinks to, keeping its shape; null when it already fits. */
export function fitLongEdge(width: number, height: number, max = IMAGE_LONG_EDGE): { width: number; height: number } | null {
  const long = Math.max(width, height);
  if (!(long > max) || width <= 0 || height <= 0) return null;
  const k = max / long;
  return { width: Math.max(1, Math.round(width * k)), height: Math.max(1, Math.round(height * k)) };
}

/** The Markdown line that names an uploaded image in a task body or a memo. */
export const imageRef = (id: string, alt = '') => `![${alt.replace(/[[\]\n]/g, ' ').trim()}](attachment:${id})`;

/** `ref` on a line of its own at the cursor (the end when there is none). */
export function insertImageRef(text: string, cursor: number | null | undefined, ref: string): string {
  const at = Math.min(Math.max(cursor ?? text.length, 0), text.length);
  const before = text.slice(0, at);
  const after = text.slice(at);
  return `${before}${before && !before.endsWith('\n') ? '\n' : ''}${ref}${after && !after.startsWith('\n') ? '\n' : ''}${after}`;
}

/** An image reference in a text: where it sits, the caption and the attachment id. */
export interface ImageSpan {
  start: number;
  end: number;
  caption: string;
  id: string;
}

/** The `![caption](attachment:ID)` references in a text, in order. */
export function findImageRefs(text: string): ImageSpan[] {
  const out: ImageSpan[] = [];
  for (const m of text.matchAll(/!\[([^\]\n]*)\]\(attachment:([A-Za-z0-9_-]+)\)/g)) {
    out.push({ start: m.index, end: m.index + m[0].length, caption: m[1]!, id: m[2]! });
  }
  return out;
}
