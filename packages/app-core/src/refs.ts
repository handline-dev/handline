// The task numbers (`T<N>`) and pull request numbers (`#42`) in a text, for the one renderer that makes them links. A task is T<N>; a #N is a
// pull request only when the chat agent's repo has one of that number (`pr`), else it is no ref.

export interface RefSpan {
  start: number;
  end: number;
  kind: 'task' | 'pr';
  id: number;
}

// Not part of a word, an entity (`&#38;`) or a path: the char before must be a boundary. `T12abc` has no boundary after the digits and is no ref.
const REF = /(?<![\w&/#.-])(?:T|(#))(\d{1,9})\b/g;

/** The `#N` numbers of a text, once each and at most `max` (what pr.exists takes). */
export function hashNumbers(text: string, max = 20): number[] {
  const ids = [...text.matchAll(REF)].filter((m) => m[1]).map((m) => Number(m[2]));
  return [...new Set(ids)].filter((n) => n > 0).slice(0, max);
}

/** The refs of a text in order. A `#N` counts only when `pr` says it is a pull request; a task number only when `task` (if given) says the id is one. */
export function findRefs(
  text: string,
  opts: { task?: (id: number) => boolean; pr?: (id: number) => boolean } = {},
): RefSpan[] {
  const out: RefSpan[] = [];
  for (const m of text.matchAll(REF)) {
    const id = Number(m[2]);
    const kind = m[1] ? 'pr' : 'task';
    if (!id || (kind === 'pr' ? !opts.pr?.(id) : opts.task && !opts.task(id))) continue;
    out.push({ start: m.index!, end: m.index! + m[0].length, kind, id });
  }
  return out;
}
