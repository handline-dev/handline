// The task numbers (`T3610`) and message numbers (`#42`) in a text, for the one renderer that makes them links. A task is T<N>, a message #N.

export interface RefSpan {
  start: number;
  end: number;
  kind: 'task' | 'message';
  id: number;
}

// Not part of a word, an entity (`&#38;`) or a path: the char before must be a boundary. `T12abc` has no boundary after the digits and is no ref.
const REF = /(?<![\w&/#.-])(?:T|(#))(\d{1,9})\b/g;

/** The refs of a text in order. A message number counts only when `messages` is set (a chat has them); a task number only when `task` (if given) says the id is one. */
export function findRefs(text: string, opts: { messages?: boolean; task?: (id: number) => boolean } = {}): RefSpan[] {
  const out: RefSpan[] = [];
  for (const m of text.matchAll(REF)) {
    const id = Number(m[2]);
    const kind = m[1] ? 'message' : 'task';
    if (!id || (kind === 'message' ? !opts.messages : opts.task && !opts.task(id))) continue;
    out.push({ start: m.index!, end: m.index! + m[0].length, kind, id });
  }
  return out;
}
