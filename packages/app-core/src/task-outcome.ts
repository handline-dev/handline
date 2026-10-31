// What a done task's evidence says about a check: PASS or FAIL, the PR, the head. tools/merge_ready.py reads the same format
// (AUDIT_PASS and UX_PASS), so a change to one changes both.
export interface TaskOutcome {
  verdict: 'PASS' | 'FAIL' | null;
  pr: number | null;
  sha: string | null;
  /** A live check of the running app (the ux check), not a code audit. */
  live: boolean;
  /** The evidence after the verdict and the head: what the checker said. */
  rest: string;
}

const SHA = '@([0-9a-f]{7,40})\\b';
const prNumber = (s: string | null | undefined) => {
  const m = /(?:PR\s*#?|#)(\d+)/i.exec(s ?? '');
  return m ? Number(m[1]) : null;
};

/** The verdict word first and the head right after, or PR and head first and the verdict after; anything else standing before it is no verdict. */
function verdictOf(e: string): { verdict: 'PASS' | 'FAIL'; rest: string } | null {
  for (const v of ['PASS', 'FAIL'] as const) {
    const first = new RegExp(`^(?:(?:UX live|Web half) )?${v}\\b[:,]?\\s*(?:on\\s+)?(?:PR\\s*)?(?:#(\\d+)\\s*)?${SHA}[:,]?\\s*`).exec(e);
    const last = new RegExp(`^(?:PR\\s*#?(\\d+)\\s+)?${SHA}\\s+${v}\\b[:,]?\\s*`).exec(e);
    const hit = first ?? last;
    if (hit) return { verdict: v, rest: e.slice(hit[0].length) };
  }
  return null;
}

export function taskOutcome(evidence: string | null | undefined, title: string | null | undefined): TaskOutcome {
  const e = (evidence ?? '').trim();
  const t = title ?? '';
  const v = verdictOf(e);
  return {
    verdict: v?.verdict ?? null,
    rest: v ? v.rest : e,
    pr: prNumber(e) ?? prNumber(t),
    sha: new RegExp(SHA).exec(e)?.[1] ?? null,
    live: /^(?:UX live|Web half)\b/.test(e) || /^(?:live[- ]check|re-?check)\b/i.test(t),
  };
}
