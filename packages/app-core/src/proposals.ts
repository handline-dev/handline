// HR proposals as the HR tab draws them: which wait on the founder, the words for each state, and the text an agent wrote made safe to show.
import type { Proposal } from '@handline/client';
import { ago } from '#src/time.ts';

/** The states that wait for the founder; any other word is drawn as text, offers no action and is not counted. */
export const isWaiting = (p: Proposal): boolean => p.state === 'open' || p.state === 'apply_failed';
export const waitingCount = (l: readonly Proposal[]): number => l.filter(isWaiting).length;

/** Approved and held until the agents in `waiting_on` end their turns: the founder has no move on it and it is not counted. */
export const isHeld = (p: Proposal): boolean => p.state === 'applying';

/** The start of an applied proposal's agent that did not go through: the change is applied, only the start waits (`waiting`, for the load) or failed. */
export type FollowUp = { agent: string; state: string; text: string };
export function followUpOf(p: Proposal): FollowUp | null {
  const f = p.follow_up;
  return p.state === 'approved' && f ? { agent: textSafe(f.agent), state: f.state, text: textSafe(f.text) } : null;
}

/** What a held proposal waits for, in words: "waits for handline.pc's turn to end", or for several "waits for a and b to end their turns"; "since 5m" when the daemon says since when. */
export function heldLine(p: Proposal, now = Date.now()): string {
  const who = (p.waiting_on ?? []).map(textSafe);
  const since = p.waiting_since ? `, since ${ago(p.waiting_since, now)}` : '';
  if (!who.length) return `waits for a turn to end${since}`;
  if (who.length === 1) return `waits for ${who[0]}'s turn to end${since}`;
  return `waits for ${who.slice(0, -1).join(', ')} and ${who.at(-1)} to end their turns${since}`;
}

const STATE_WORDS: Record<string, { word: string; tone: 'wait' | 'block' | 'run' | 'muted' }> = {
  open: { word: 'needs you', tone: 'wait' },
  apply_failed: { word: 'approved, not applied', tone: 'block' },
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

/** A waiting proposal nobody answered in this many days is stale: it moves to its own group, still open to decide. */
export const STALE_DAYS = 14;
export const splitStale = (waiting: readonly Proposal[], now = Date.now()): { fresh: Proposal[]; stale: Proposal[] } => {
  const old = (p: Proposal) => now - Date.parse(p.created) > STALE_DAYS * 86_400_000;
  return { fresh: waiting.filter((p) => !old(p)), stale: waiting.filter(old) };
};

/** The kind, from the proposal or else the first word of its title; an unknown kind is drawn as its own word. */
export const kindOf = (p: Proposal): string => p.kind ?? p.title.split(' ')[0] ?? '';

/** Text from an agent: control characters dropped (a tab and a newline stay), nothing else touched; React draws it as text. */
// eslint-disable-next-line no-control-regex
export const textSafe = (s: string): string => s.replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '');
export const cutError = (s: string, n = 400): string => (s.length > n ? `${s.slice(0, n)}…` : s);

/** A message number an agent put in its words ("msg_8aebff7", "ocp-msg 1a2b3c4d"): it means nothing to the person reading, so it is not shown. */
const MESSAGE_REF = /[ \t]*\(?\b(?:msg_[0-9a-f]{7}|ocp-msg [0-9a-f]{8,})\b\)?/g;
/** An agent's words made safe to show, without its message numbers. */
export const plainText = (s: string): string =>
  textSafe(s)
    .replace(MESSAGE_REF, '')
    .replace(/[ \t]+([,.;:])/g, '$1')
    .trim();

/** A bundle is one card for several changes, decided as a whole. */
export const isBundle = (p: Proposal): boolean => kindOf(p) === 'bundle';

/** The words of the button that opens the list of changes: a bundle counts its changes, any other says diff. */
export function changesToggle(p: Proposal, open: boolean): string {
  const n = diffLines(p).length;
  const many = `${n} change${n === 1 ? '' : 's'}`;
  if (isBundle(p)) return open ? 'Hide changes' : `Show ${many}`;
  return open ? 'Hide diff' : `Show diff (${many})`;
}

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

export interface DecideResult {
  id: number;
  title: string;
  ok: boolean;
  /** The refusal in words, when the daemon refused this one. */
  error?: string;
}
type DecideCall = (arg: { proposal: number; decision: 'approve' | 'reject'; note?: string; subject: string }) => Promise<unknown>;

/** Decide the given proposals one after another, each for the text it showed; a refusal is recorded and the rest go on. */
export async function decideMany(
  list: readonly Proposal[],
  decision: 'approve' | 'reject',
  note: string,
  call: DecideCall,
  say: (e: unknown) => string,
): Promise<DecideResult[]> {
  const out: DecideResult[] = [];
  for (const p of list) {
    try {
      await call({ proposal: p.id, decision, ...(decision === 'reject' ? { note } : {}), subject: p.subject });
      out.push({ id: p.id, title: p.title, ok: true });
    } catch (e) {
      out.push({ id: p.id, title: p.title, ok: false, error: say(e) });
    }
  }
  return out;
}

/** An approved proposal that did not apply: one line the person reads, and the daemon's own words for Details. The line's reason is the
 *  error's first sentence without a "not applied" lead of its own, and left out when it says "applied" (it would contradict the headline). */
export function applyFailure(p: Proposal): { line: string; details: string | null } {
  const raw = p.error ? textSafe(p.error).trim() : '';
  const first = (raw.replace(/^(?:proposal\s+\S+\s+)?(?:was\s+)?not applied:?\s*/i, '').split(/\.\s|\n/)[0] ?? '')
    .replace(/\.$/, '')
    .trim();
  const reason = /\bapplied\b/i.test(first) ? '' : first.length > 100 ? `${first.slice(0, 100)}…` : first;
  return { line: `Approved, but not applied${reason ? `: ${reason}` : ''}.`, details: raw && raw !== reason ? cutError(raw) : null };
}

/** An accepted decision whose proposal now reads apply_failed did not apply: that row fails with the line above. */
export function settled(rows: readonly DecideResult[], now: readonly Proposal[]): DecideResult[] {
  return rows.map((r) => {
    const p = now.find((x) => x.id === r.id);
    return r.ok && p?.state === 'apply_failed' ? { ...r, ok: false, error: applyFailure(p).line } : r;
  });
}
