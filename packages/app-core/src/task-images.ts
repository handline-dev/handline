// How text names an uploaded image (docs/story1-task-images-graph.md section A): `![caption](attachment:<id>)`, in a task body, a memo
// or a reject note. The daemon cuts a text into parts on read; this writes the reference.

/** One image reference. A caption cannot hold the brackets of the form. */
export const imageRef = (id: string, caption = 'capture'): string => `![${caption.replace(/[[\]]/g, '')}](attachment:${id})`;

/** A note, then one reference per image, each on its own line. */
export const withImages = (note: string, ids: readonly string[], caption = 'capture'): string =>
  [note.trim(), ...ids.map((id) => imageRef(id, caption))].filter(Boolean).join('\n');

/** A task title from the first line of a note: its first 60 characters, or `fallback` when the note is empty. */
export function titleFromNote(note: string, fallback: string): string {
  const line = note.trim().split('\n')[0]?.trim() ?? '';
  return line ? (line.length > 60 ? `${line.slice(0, 59)}…` : line) : fallback;
}

export interface ImageRef {
  id: string;
  name: string;
  mime: 'image/png';
  bytes: number;
}
export type TextPart =
  { text: string } | { image: { id: string; name?: string; mime?: string; bytes?: number }; caption: string; gone?: boolean };

const REF = /!\[([^\]]*)\]\(attachment:([0-9A-Za-z-]+)\)/g;

/** The text cut at its references, as the daemon's `body_parts` / `parts` are; null when it names no image. For a daemon that does not send them. */
export function cutText(text: string): TextPart[] | null {
  const out: TextPart[] = [];
  let at = 0;
  for (const m of text.matchAll(REF)) {
    if (m.index > at) out.push({ text: text.slice(at, m.index) });
    out.push({ image: { id: m[2]!, name: m[1] || 'image', mime: 'image/png', bytes: 0 }, caption: m[1]! });
    at = m.index + m[0].length;
  }
  if (!out.length) return null;
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

/** The text without its image references, and the images it names (the kept ones, from the daemon's parts when it sent them). */
export function splitImages(
  text: string,
  parts?: TextPart[],
): { text: string; images: { id: string; name?: string; mime?: string; bytes?: number }[] } {
  const cut = parts ?? cutText(text);
  if (!cut) return { text, images: [] };
  return {
    text: cut
      .map((p) => ('text' in p ? p.text : ''))
      .join('')
      .trim(),
    images: cut.flatMap((p) => ('image' in p && !p.gone ? [p.image] : [])),
  };
}
