// The fake daemon's usage numbers: stats.get and stats.outlook, made from the clock it is given.
import type { Outlook, Stats, Timestamp } from '@handline/contract';
import { badRequest } from '#src/errors.ts';

export const HOUR = 3_600_000;
export const DAY = 24 * HOUR;
export const AGENTS = ['handline.architect', 'handline.auditor', 'handline.term-build'];

/** What the outlook_agent_use fixtures name (--var outlook_agent=grp.one outlook_group=grp): two agents of the group grp. */
const AGENT_USE = [
  { agent: 'grp.one', tokens: 9000, cost: 3.5, share: 0.7, est_pct: 4.2, fracs: { alpha: 0.7 } },
  { agent: 'grp.two', tokens: 3000, cost: 1.5, share: 0.3, est_pct: null, fracs: { alpha: 0.3 } },
];
const OVERLAYS: Record<string, number[]> = {
  'grp.one': [0, 1.5, 2.9, 4, 5, 5.5, 6, 6.2, 6.4, 6.6, 6.8, 7, 7.2],
  'grp.two': [0, 0.5, 1.2, 1.5, 1.8, 2, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7],
  'grp:grp': [0, 2, 4.1, 5.5, 6.8, 7.5, 8.1, 8.4, 8.7, 9, 9.3, 9.6, 9.9],
};

/** stats.outlook's by_account: alpha a little above the average, beta below it, so the two average to about `left_pct`; alpha's week resets as its account row says. The fake does not pace, so there is no line. */
function byAccount(past: Outlook['past'], future: Outlook['future'], now: number, head: number): Outlook['by_account'] {
  const points = (pts: Outlook['past'], d: number) =>
    pts.map((p) => ({
      at: p.at,
      five_left: null,
      week_left: p.left_pct === null ? null : Math.min(100, Math.max(0, p.left_pct + d)),
      line_left: null,
    }));
  const reset = now + 70 * HOUR; // the same reset alpha's account row carries
  return [
    {
      account: 'alpha',
      past: points(past, 5),
      future: points(future, 5),
      resets: [
        { at: new Date(now - head / 2).toISOString(), window: '7d' as const },
        ...(reset <= now + head ? [{ at: new Date(reset).toISOString(), window: '7d' as const }] : []),
      ],
    },
    { account: 'beta', past: points(past, -5), future: points(future, -5), resets: [] },
  ];
}
/** [agent, account, tokens, list-price cost]: the architect and auditor share alpha with the founder's own sessions (agent ''), the auditor also used beta, which the architect moved to. */
const CALLS: [string, string, number, number][] = [
  [AGENTS[0]!, 'alpha', 9_200_000, 18],
  [AGENTS[1]!, 'alpha', 3_100_000, 6],
  ['', 'alpha', 3_000_000, 6],
  [AGENTS[0]!, 'beta', 1_000_000, 2],
  [AGENTS[1]!, 'beta', 1_500_000, 3],
  [AGENTS[2]!, 'gamma', 400_000, 1],
];
/** Points each account's week rose in the window; gamma has no reading. */
const RISE: Record<string, { '24h': number; '7d': number }> = { alpha: { '24h': 8, '7d': 30 }, beta: { '24h': 3, '7d': 10 } };

/** Each row's week_pct is its share of the account's cost times the account's rise, so an account's rows add up to its rise. */
function useOf(week: boolean): Stats['use'] {
  const scale = week ? 1 : 0.25;
  const cost = new Map<string, number>();
  for (const [, account, , c] of CALLS) cost.set(account, (cost.get(account) ?? 0) + c);
  return CALLS.map(([agent, account, tokens, c]) => {
    const share = c / cost.get(account)!;
    const rise = RISE[account]?.[week ? '7d' : '24h'];
    return {
      agent,
      account,
      tokens: Math.round(tokens * scale),
      cost: Math.round(c * scale * 100) / 100,
      share: Math.round(share * 10_000) / 10_000,
      week_pct: rise === undefined ? null : Math.round(share * rise * 100) / 100,
    };
  }).sort((a, b) => b.cost - a.cost);
}

export function statsOf(nowIso: Timestamp, window: '24h' | '7d'): Stats {
  const until = Date.parse(nowIso);
  const week = window === '7d';
  return {
    window,
    since: new Date(until - (week ? 7 * DAY : DAY)).toISOString(),
    until: new Date(until).toISOString(),
    tasks_done: week ? 19 : 3,
    use: useOf(week),
    moves: week ? 6 : 1,
    handoffs: week ? 9 : 2,
    blocks: week ? 4 : 0,
    per_account: [
      { account: 'alpha', five_hour_used_pct: 41, weekly_used_pct: 62 },
      { account: 'beta', five_hour_used_pct: 88, weekly_used_pct: 30 },
      { account: 'gamma', five_hour_used_pct: null, weekly_used_pct: null },
    ],
  };
}

export function outlookOf(nowIso: Timestamp, range: Outlook['range'], pick?: unknown): Outlook {
  if (!['12h', '24h', '3d', '7d', '14d', '30d'].includes(range)) throw badRequest(`no range ${range}`, 'range');
  const now = Date.parse(nowIso);
  const span = { '12h': 12 * HOUR, '24h': DAY, '3d': 3 * DAY, '7d': 7 * DAY, '14d': 14 * DAY, '30d': 30 * DAY }[range];
  const head = Math.min(span, 14 * DAY);
  const steps = 12;
  const at = (t: number) => new Date(t).toISOString();
  const past = Array.from({ length: steps + 1 }, (_, i) => ({
    at: at(now - head + (head * i) / steps),
    left_pct: 90 - (i * 40) / steps,
  }));
  const future = Array.from({ length: steps }, (_, i) => ({
    at: at(now + (head * (i + 1)) / steps),
    left_pct: Math.max(0, 50 - ((i + 1) * 50) / steps),
  }));
  if (pick !== undefined && typeof pick !== 'string') throw badRequest('agent is a string', 'agent');
  const days = Math.max(1, Math.min(30, Math.round(span / DAY)));
  const cost_per_day = Array.from({ length: days }, (_, i) => ({
    day: at(now - (days - 1 - i) * DAY).slice(0, 10),
    agents: { [AGENTS[0]!]: 4 + (i % 3), [AGENTS[1]!]: 1 + (i % 2), [AGENTS[2]!]: i % 4 === 0 ? 0 : 2 },
  }));
  return {
    range,
    now: at(now),
    accounts: 2,
    past,
    future,
    runway: [
      { account: 'alpha', runs_out_in_seconds: null, week_resets_at: at(now + 70 * HOUR) },
      { account: 'beta', runs_out_in_seconds: 2 * 3600, week_resets_at: null },
    ],
    cost_per_day,
    agent_use: AGENT_USE.map((u) => ({ ...u, fracs: { ...u.fracs } })),
    by_account: byAccount(past, future, now, head),
    ...(pick !== undefined && {
      overlay: (Object.hasOwn(OVERLAYS, pick) ? OVERLAYS[pick]! : []).map((v, i) => (i < past.length ? v : 0)).slice(0, past.length),
    }),
  };
}
