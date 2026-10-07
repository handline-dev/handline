// HR proposals as the HR tab draws them: which wait on the founder, the words for each state, and the text an agent wrote made safe to show.
import type { Proposal } from '@handline/client';

/** The states that wait for the founder; any other word is drawn as text, offers no action and is not counted. */
export const isWaiting = (p: Proposal): boolean => p.state === 'open' || p.state === 'apply_failed';
export const waitingCount = (l: readonly Proposal[]): number => l.filter(isWaiting).length;

const STATE_WORDS: Record<string, { word: string; tone: 'wait' | 'block' | 'run' | 'muted' }> = {
  open: { word: 'waiting for you', tone: 'wait' },
  apply_failed: { word: 'approved, apply failed', tone: 'block' },
  approved: { word: 'approved and applied', tone: 'run' },
  rejected: { word: 'rejected', tone: 'block' },
  withdrawn: { word: 'withdrawn', tone: 'muted' },
};
export const stateWord = (p: Proposal) => STATE_WORDS[p.state] ?? { word: p.state, tone: 'muted' as const };

/** Waiting ones first and oldest first; settled ones after, newest settled first. */
export function ordered(l: readonly Proposal[]): { waiting: Proposal[]; recent: Proposal[] } {
  const at = (t: string | null | undefined) => (t ? Date.parse(t) : 0);
  return {
    waiting: l.filter(isWaiting).sort((a, b) => at(a.created) - at(b.created)),
    recent: l.filter((p) => !isWaiting(p)).sort((a, b) => at(b.settled ?? b.created) - at(a.settled ?? a.created)),
  };
}

/** The kind, from the proposal or else the first word of its title; an unknown kind is drawn as its own word. */
export const kindOf = (p: Proposal): string => p.kind ?? p.title.split(' ')[0] ?? '';

/** Text from an agent: control characters dropped (a tab and a newline stay), nothing else touched; React draws it as text. */
// eslint-disable-next-line no-control-regex
export const textSafe = (s: string): string => s.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '');
export const cutError = (s: string, n = 400): string => (s.length > n ? `${s.slice(0, n)}…` : s);

/** The asked fields in the bridge's order: vendor, model, effort, team, repo, to, then each `set <key>`. */
export function askedLines(p: Proposal): [string, string][] {
  const a = p.asked;
  if (!a || kindOf(p) === 'bundle') return [];
  const out: [string, string][] = [];
  for (const k of ['vendor', 'model', 'effort', 'team', 'repo', 'to'] as const) if (a[k]) out.push([k, textSafe(a[k]!)]);
  for (const [k, v] of Object.entries(a.sets ?? {})) out.push([`set ${k}`, textSafe(v)]);
  return out;
}

/** One change per line; a line starting with - or + is a removal or an addition. */
export const diffLines = (p: Proposal): { text: string; mark: '-' | '+' | '' }[] =>
  textSafe(p.diff)
    .split('\n')
    .filter((l) => l.length)
    .map((text) => ({ text, mark: text.startsWith('-') ? '-' : text.startsWith('+') ? '+' : '' }));
