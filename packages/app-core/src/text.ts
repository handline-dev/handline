// Short forms of longer text, for rows, cards and search hits. Pure.

/** `s`, cut to `n` characters with ‘…’ when it was longer. */
export const ellipsis = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n)}…` : s);

/** `s` on one line, whitespace runs folded to a space, at most `max` characters. */
export const oneLine = (s: string, max: number): string => s.replace(/\s+/g, ' ').trim().slice(0, max);

/** A tool call's input in one line: its first string value, or the whole input as JSON. */
export function toolInputLine(input: Record<string, unknown>, max: number): string {
  const first = Object.values(input).find((v) => typeof v === 'string');
  return oneLine(typeof first === 'string' ? first : JSON.stringify(input), max);
}

const SNIPPET = 90;

/** The words around the first match of `query`: ‘…’ marks a cut end. */
export function searchSnippet(body: string, query: string): string {
  const flat = body.replace(/\s+/g, ' ').trim();
  const from = Math.max(0, flat.toLowerCase().indexOf(query.toLowerCase()) - 30);
  return `${from > 0 ? '…' : ''}${flat.slice(from, from + SNIPPET)}${from + SNIPPET < flat.length ? '…' : ''}`;
}
