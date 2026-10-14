// A text cut at its image references (docs/story1-task-images-graph.md section A): `![caption](attachment:<id>)` is the one form read.
// Not done yet: a swept image is refused as bad_request (the doc says not_found); an edit that keeps an old reference is refused if its image
// is gone (the doc never refuses it); no agent role rule; a reject note and the timeline rows carry no parts.
import type { Attachment } from '@handline/contract';

export type Part = { text: string } | { image: Attachment | { id: string }; caption: string; gone?: true };

const REF = /!\[([^\]]*)\]\(attachment:([0-9A-Za-z-]+)\)/g;
export const MAX_REFS = 6;

/** The ids a text names, in order. */
export const refs = (text: string): string[] => [...text.matchAll(REF)].map((m) => m[2]!);

/** The parts of a text, or null when it names no image. */
export function cut(text: string, kept: (id: string) => Attachment | undefined): Part[] | null {
  const parts: Part[] = [];
  let at = 0;
  for (const m of text.matchAll(REF)) {
    if (m.index > at) parts.push({ text: text.slice(at, m.index) });
    const image = kept(m[2]!);
    parts.push(image ? { image, caption: m[1]! } : { image: { id: m[2]! }, caption: m[1]!, gone: true });
    at = m.index + m[0].length;
  }
  if (!parts.length) return null;
  if (at < text.length) parts.push({ text: text.slice(at) });
  return parts;
}
