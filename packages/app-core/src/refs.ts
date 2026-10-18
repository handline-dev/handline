// The task numbers (`T<N>`) and message numbers (`#42`) in a text, for the one renderer that makes them links. A task is T<N>, a message #N,
// unless the chat agent's repo has a pull request of that number (`pr`): then it is the pull request.

export interface RefSpan {
  start: number;
  end: number;
  kind: 'task' | 'message' | 'pr';
  id: number;
}

// Not part of a word, an entity (`&#38;`) or a path: the char before must be a boundary. `T12abc` has no boundary after the digits and is no ref.
const REF = /(?<![\w&/#.-])(?:T|(#))(\d{1,9})\b/g;

/** The `#N` numbers of a text, once each and at most `max` (what pr.exists takes). */
export function hashNumbers(text: string, max = 20): number[] {
  const ids = [...text.matchAll(REF)].filter((m) => m[1]).map((m) => Number(m[2]));
  return [...new Set(ids)].filter((n) => n > 0).slice(0, max);
}

/** The refs of a text in order. A `#N` is a pull request when `pr` says so, else a message number, which counts only when `messages` is set (a chat has them); a task number only when `task` (if given) says the id is one. */
export function findRefs(
  text: string,
  opts: { messages?: boolean; task?: (id: number) => boolean; pr?: (id: number) => boolean } = {},
): RefSpan[] {
  const out: RefSpan[] = [];
  for (const m of text.matchAll(REF)) {
    const id = Number(m[2]);
    const kind = m[1] ? (opts.pr?.(id) ? 'pr' : 'message') : 'task';
    if (!id || (kind === 'message' ? !opts.messages : kind === 'task' && opts.task && !opts.task(id))) continue;
    out.push({ start: m.index!, end: m.index! + m[0].length, kind, id });
  }
  return out;
}
