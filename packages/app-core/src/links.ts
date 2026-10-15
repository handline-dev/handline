// Words in a message that may name a git branch, for opening a file path as that branch has it. Only a guess: the shell asks git which
// of them exist. A branch name has a `-` or `/` in it (`story/4153`, `pr-review-loop-review`); `main` is where the shell falls back to.

const WORD = /(?<![\w@:/.-])[\w][\w./-]{2,199}/g;

/** Up to 20 distinct words of the text that look like a branch name, in order. */
export function branchCandidates(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(WORD)) {
    const w = m[0].replace(/[.,;)]+$/, '');
    if (!/[-/]/.test(w) || /^[\d./-]+$/.test(w) || w.includes('//') || w.endsWith('/') || out.includes(w)) continue;
    out.push(w);
    if (out.length === 20) break;
  }
  return out;
}
