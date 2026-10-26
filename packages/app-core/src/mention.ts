// The "@name" typed at the caret: which word is being typed and which agents it may become. Pure.

/** The `@word` that ends the text before the caret (at the start or after a space), or null. */
export function mentionAt(before: string): { query: string; length: number } | null {
  const m = /(?:^|\s)@([\w.-]*)$/.exec(before);
  return m ? { query: m[1]!, length: m[1]!.length + 1 } : null;
}

/** Agents for a typed query, the ones near the task first, then those that start with it before those that only contain it; at most `limit`. */
export function mentionNames(query: string, names: readonly string[], near: readonly string[] = [], limit = 6): string[] {
  const q = query.toLowerCase();
  const rank = (n: string) => {
    const last = n.split('.').at(-1)!.toLowerCase();
    const hit = !q ? 0 : last.startsWith(q) ? 0 : n.toLowerCase().startsWith(q) ? 1 : n.toLowerCase().includes(q) ? 2 : -1;
    return hit < 0 ? null : (near.includes(n) ? 0 : 10) + hit;
  };
  return [...new Set(names)]
    .flatMap((n) => {
      const r = rank(n);
      return r === null ? [] : [{ n, r }];
    })
    .sort((a, b) => a.r - b.r || a.n.localeCompare(b.n))
    .slice(0, limit)
    .map((x) => x.n);
}

/** What an agent is doing, in the words next to its name in the list: "needs you", "working" or "idle". */
export const mentionState = (a: { state: string; needs_you?: unknown }): string =>
  a.needs_you || a.state === 'waiting' ? 'needs you' : a.state === 'running' ? 'working' : 'idle';
