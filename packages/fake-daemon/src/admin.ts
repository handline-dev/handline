// The fake daemon's read-mostly admin surface: the stats windows, the usage outlook, the home settings, the standing answers, and the repos
// and worktrees (worktrees.ts).
import type { ConnectionInfo, Headroom, Outlook, Settings, SettingsChanges, StandingAnswer, Stats, Timestamp } from '@handline/contract';
import { Failure, badRequest } from '#src/errors.ts';
import { FakeWorktrees } from '#src/worktrees.ts';
import { FakePrefs, type PrefsHost } from '#src/prefs.ts';

type Accepted = (said: string) => unknown;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const FINGERPRINT = 'sha256:3f9c1e7a52b84d06a1e3c7f90b2d4856e1a7c3095bd8f2e46a0c5d71b93e8f24';
const AGENTS = ['handline.architect', 'handline.auditor', 'handline.term-build'];

/** The methods this answers. */
const ADMIN = /^(stats\.|connection\.get|settings\.|answers?\.|devices?\.(list|revoke)|agent\.triggers|memory\.status|events\.feed)/;
/** Every settings value at the Go daemon's default (go/internal/reads/settings.go), except active_cap: the fake keeps 4. */
const DEFAULTS: Settings['values'] = {
  active_cap: 4,
  park_timeout_s: 1800,
  handoff_note: true,
  ignore_cache_expiry: false,
  dev_mode: false,
  bg_notice_s: 1800,
  bg_grace_s: 300,
  bg_check_s: 600,
  notice_digest_s: 300,
  token_prices: {},
  input_hold_idle_s: 600,
  handoff_prompt:
    "Write your handoff to {path} now, while your prompt cache is warm, with one Write (replace what is there), then stop and run nothing else. The next session of you, if it starts fresh, reads it first: copy yourself into it. At most {max} characters, about 30 lines, only what `hl` and your memory cannot tell it: (1) what you were deciding and why; (2) what you meant to do next; (3) promises or doubts not yet on any task; (4) the founder's preferences and the pitfalls you learned this session; (5) who you wait on, as T<n>/#<n> numbers only. Do not restate what a task or message holds; cite its T or # number. A lasting lesson or preference goes to your memory first, not here. If there is nothing, write the single word: nothing.",
  push_prompt_asked: true,
  push_task_done: true,
  push_stall: true,
  push_account_out: true,
  host_active_cap: 0,
  load_per_core_pct: 150,
  ram_free_min_pct: 10,
};

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

/** stats.outlook's by_account: alpha a little above the average, beta below it, so the two average to about `left_pct`; alpha's week resets in 2 days, as its runway says. The fake does not pace, so there is no line. */
function byAccount(past: Outlook['past'], future: Outlook['future'], now: number, head: number): Outlook['by_account'] {
  const points = (pts: Outlook['past'], d: number) =>
    pts.map((p) => ({
      at: p.at,
      five_left: null,
      week_left: p.left_pct === null ? null : Math.min(100, Math.max(0, p.left_pct + d)),
      line_left: null,
    }));
  const reset = now + 2 * DAY;
  return [
    {
      account: 'alpha',
      past: points(past, 5),
      future: points(future, 5),
      resets: reset <= now + head ? [{ at: new Date(reset).toISOString(), window: '7d' as const }] : [],
    },
    { account: 'beta', past: points(past, -5), future: points(future, -5), resets: [] },
  ];
}

export class FakeAdmin {
  readonly worktrees: FakeWorktrees;
  readonly prefs: FakePrefs;
  /** A daemon behind a gateway has no listener of its own: connection.get then answers null. */
  noListener = false;
  private now: () => Timestamp;
  private on: boolean;
  private values: Settings['values'] = structuredClone(DEFAULTS);
  private devices = [
    {
      id: 'd1',
      name: 'Handline desktop',
      role: 'founder' as const,
      created: '2026-10-01T09:00:00Z',
      last_seen: '2026-10-16T01:00:00Z',
      connected: true,
    },
    {
      id: 'd2',
      name: 'phone-3b54',
      role: 'founder' as const,
      created: '2026-10-05T09:00:00Z',
      last_seen: '2026-10-12T08:00:00Z',
      connected: false,
    },
  ];
  private answers: StandingAnswer[] = [
    { kind: 'trust', key: ['/work/handline.architect'], text: 'trust folder /work/handline.architect' },
    { kind: 'dialog', key: ['claude', 'Do you trust the files', 'Yes, I trust'], text: 'claude: Do you trust the files → Yes, I trust' },
  ];

  /** `on` false is a daemon that does not serve stats, settings or standing answers: it lists none of their methods. */
  constructor(now: () => Timestamp, on: boolean, host: PrefsHost) {
    this.now = now;
    this.on = on;
    this.worktrees = new FakeWorktrees(on);
    this.prefs = new FakePrefs(host);
  }

  /** Whether the daemon lists this method. */
  lists = (name: string): boolean => (this.on || !ADMIN.test(name)) && this.worktrees.lists(name);

  /** Whether this class answers the method. */
  owns = (name: string): boolean => ADMIN.test(name) || this.worktrees.owns(name) || this.prefs.owns(name);

  call(method: string, p: Record<string, unknown>, accepted: Accepted): unknown {
    if (this.worktrees.owns(method)) return this.worktrees.call(method, p);
    if (this.prefs.owns(method)) return this.prefs.call(method, p, accepted);
    switch (method) {
      case 'stats.get':
        return this.stats(p.window === '7d' ? '7d' : '24h');
      case 'stats.outlook':
        return this.outlook(String(p.range) as Outlook['range'], p.agent);
      case 'stats.headroom':
        return this.headroom(p);
      case 'connection.get':
        return this.connection();
      case 'settings.get':
        return this.settings();
      case 'settings.set':
        return (this.set((p.changes ?? {}) as SettingsChanges), accepted('Saved.'));
      case 'agent.triggers':
        return { agents: AGENTS.map((agent, i) => ({ agent, fired: i === 0, reason: i === 0 ? 'a task arrived' : 'nothing to wake it' })) };
      case 'memory.status':
        return {
          agents: AGENTS.map((agent, i) => ({
            agent,
            bytes: 2000 * (i + 1),
            budget: 8000,
            entries: 3 * (i + 1),
            unused: i === 2 ? ['old-note'] : [],
          })),
        };
      case 'events.feed':
        return {
          events: [
            {
              cursor: 2,
              topic: 'tasks',
              type: 'task.changed',
              at: this.now(),
              actor: null,
              agent: AGENTS[1],
              title: 'Task T7 done',
              target: null,
            },
            {
              cursor: 1,
              topic: 'agents',
              type: 'agent.state',
              at: this.now(),
              actor: null,
              agent: AGENTS[0],
              title: 'Started',
              target: null,
            },
          ],
          cursor: 2,
          gap: false,
        };
      case 'devices.list':
        return { devices: this.devices.map((d) => ({ ...d })) };
      case 'device.revoke':
        if (!this.devices.some((d) => d.id === p.device)) throw badRequest('no such device', 'device');
        return ((this.devices = this.devices.filter((d) => d.id !== p.device)), accepted('Revoked.'));
      case 'answers.list':
        return { answers: this.answers.map((a) => ({ ...a, key: [...a.key] })) };
      case 'answer.add':
        return (this.add(p), accepted('Added.'));
      case 'answer.remove':
        return (this.remove(p), accepted('Removed.'));
      default:
        throw new Failure('refused', `the fake daemon does not serve ${method}`, { reason: 'unsupported', gate: 'unsupported' });
    }
  }

  stats(window: '24h' | '7d'): Stats {
    const until = Date.parse(this.now());
    const week = window === '7d';
    return {
      window,
      since: new Date(until - (week ? 7 * DAY : DAY)).toISOString(),
      until: new Date(until).toISOString(),
      tasks_done: week ? 19 : 3,
      working_seconds: { [AGENTS[0]!]: week ? 61_200 : 11_520, [AGENTS[1]!]: week ? 20_000 : 3_600, [AGENTS[2]!]: 0 },
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

  outlook(range: Outlook['range'], pick?: unknown): Outlook {
    if (!['12h', '24h', '3d', '7d', '14d', '30d'].includes(range)) throw badRequest(`no range ${range}`, 'range');
    const now = Date.parse(this.now());
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
        { account: 'alpha', runs_out_in_seconds: null, week_resets_at: at(now + 2 * DAY) },
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

  /** Two accounts: alpha's 5h window resets every 5 hours from 2 hours on and fills a little faster than beta's; beta runs low on its week. */
  headroom(p: Record<string, unknown>): Headroom {
    const now = Date.parse(this.now());
    const time = (key: string, fallback: number): number => {
      if (p[key] === undefined || p[key] === null) return fallback;
      const t = typeof p[key] === 'string' && /^\d{4}-\d\d-\d\dT/.test(p[key] as string) ? Date.parse(p[key] as string) : NaN;
      if (Number.isNaN(t)) throw badRequest(`${key} is an RFC 3339 timestamp`, key);
      return Math.floor(t / 1000) * 1000;
    };
    const from = time('from', now - 3 * DAY);
    const to = time('to', now + 3 * DAY);
    const step = p.step_s === undefined || p.step_s === null ? 900 : p.step_s;
    if (!Number.isInteger(step)) throw badRequest('step_s is an integer', 'step_s');
    const stepMs = (step as number) * 1000;
    if (to <= from) throw badRequest('to is after from', 'to');
    if ((step as number) < 60) throw badRequest('step_s is at least 60', 'step_s');
    if (Math.floor((to - from) / stepMs) + 1 > 2000) throw badRequest('step_s is too small for the span: at most 2000 points', 'step_s');
    const at = (t: number) => new Date(t).toISOString().replace(/\.\d{3}Z$/, 'Z');
    const FIVE = 5 * HOUR;
    const firstReset = now + 2 * HOUR;
    const saw = (t: number, perHour: number) => Math.max(0, 100 - (perHour * ((((t - firstReset) % FIVE) + FIVE) % FIVE)) / HOUR);
    const plans = [
      { account: 'alpha', five: (t: number) => saw(t, 15), week: (t: number) => Math.max(0, 70 - ((t - now) / DAY) * 8) },
      { account: 'beta', five: (t: number) => saw(t, 10), week: (t: number) => Math.max(0, 30 - ((t - now) / DAY) * 12) },
    ];
    const times: number[] = [];
    for (let t = from; t <= to; t += stepMs) times.push(t);
    const round = (v: number) => Math.round(v * 100) / 100;
    const accounts = plans.map((a) => {
      const point = (t: number) => ({ at: at(t), five_left: round(a.five(t)), week_left: round(a.week(t)) });
      const resets: Headroom['accounts'][number]['resets'] = [];
      for (let r = firstReset - Math.ceil((firstReset - from) / FIVE) * FIVE; r <= to; r += FIVE)
        if (r >= from) resets.push({ at: at(r), window: '5h' });
      return {
        account: a.account,
        past: times.filter((t) => t <= now).map(point),
        future: times.filter((t) => t > now).map(point),
        resets,
      };
    });
    const combined = (t: number) => ({
      at: at(t),
      left_pct: round(plans.reduce((sum, a) => sum + Math.min(a.five(t), a.week(t)), 0) / plans.length),
    });
    const future = times.filter((t) => t > now).map(combined);
    const next = firstReset > now && firstReset <= to ? firstReset : null;
    return {
      now: at(now),
      step_s: step as number,
      combined: { past: times.filter((t) => t <= now).map(combined), future },
      accounts,
      runs_out_at: future.find((q) => q.left_pct !== null && q.left_pct <= 0)?.at ?? null,
      next_reset: next === null ? null : { at: at(next), account: 'alpha', window: '5h' },
    };
  }

  /** A TLS listener on a Tailscale-less address, one heavy slot, dev mode as the settings say. */
  connection(): ConnectionInfo {
    return {
      served_by: 'daemon',
      listener: this.noListener
        ? null
        : { address: '192.168.1.20:7777', encryption: 'tls', fingerprint: FINGERPRINT, tailscale_host: null },
      heavy_slots: { slots: 1, dir: '/home/fake/.handline/heavy-slots' },
      dev_mode: this.values.dev_mode === true,
    };
  }

  settings(): Settings {
    const values = structuredClone(this.values) as Record<string, unknown>;
    return {
      timings: Object.fromEntries(Object.entries(values).filter(([k]) => k.endsWith('_s'))),
      terminal_host: { choice: 'fake', available: ['fake'], health: 'ok' },
      trust_folders: [],
      hooks: { installed: true, accounts: [] },
      values,
    } as unknown as Settings;
  }

  set(changes: SettingsChanges): void {
    for (const [key, value] of Object.entries(changes)) {
      if (key === 'active_cap' && value !== null && (!Number.isInteger(value) || (value as number) < 1))
        throw badRequest('active_cap is a whole number from 1', 'active_cap');
      if (key === 'park_timeout_s' && value !== null && (!Number.isInteger(value) || (value as number) < 0))
        throw badRequest('park_timeout_s is a whole number of seconds, 0 for never', 'park_timeout_s');
      const values = this.values as Record<string, unknown>;
      if (value === null) {
        values[key] = structuredClone(DEFAULTS[key as keyof typeof DEFAULTS]);
      } else values[key] = value;
    }
  }

  private add(p: Record<string, unknown>): void {
    const folder = typeof p.folder === 'string' ? p.folder.trim() : '';
    const [vendor, title, label] = ['vendor', 'title', 'label'].map((k) => (typeof p[k] === 'string' ? (p[k] as string).trim() : ''));
    const row: StandingAnswer | null = folder
      ? { kind: 'trust', key: [folder], text: `trust folder ${folder}` }
      : vendor && title && label
        ? { kind: 'dialog', key: [vendor, title, label], text: `${vendor}: ${title} → ${label}` }
        : null;
    if (!row) throw badRequest('give a folder, or vendor, title and label', 'folder');
    if (!this.answers.some((a) => a.kind === row.kind && a.key.join('\0') === row.key.join('\0'))) this.answers.push(row);
  }

  private remove(p: Record<string, unknown>): void {
    const key = Array.isArray(p.key) ? (p.key as string[]).join('\0') : '';
    const at = this.answers.findIndex((a) => a.kind === p.kind && a.key.join('\0') === key);
    if (at < 0) throw new Failure('not_found', 'no such standing answer');
    this.answers.splice(at, 1);
  }
}
