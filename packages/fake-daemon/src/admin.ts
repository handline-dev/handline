// The fake daemon's read-mostly admin surface: the stats windows, the usage outlook, the home settings and the standing answers.
import type { Outlook, Settings, SettingsChanges, StandingAnswer, Stats, Timestamp } from '@handline/contract';
import { Failure, badRequest } from './errors.ts';

type Accepted = (said: string) => unknown;
const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const AGENTS = ['handline.architect', 'handline.auditor', 'handline.term-build'];

/** The methods this answers. */
const ADMIN = /^(stats\.|settings\.|answers?\.)/;
const DEFAULTS = { active_cap: 4, park_timeout_s: 1800 };

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

export class FakeAdmin {
  private now: () => Timestamp;
  private on: boolean;
  private values: Settings['values'] = {
    active_cap: 4,
    park_timeout_s: 1800,
    handoff_note: true,
    auto_accept_dialogs: false,
    ignore_cache_expiry: false,
  };
  private answers: StandingAnswer[] = [
    { kind: 'trust', key: ['/work/handline.architect'], text: 'trust folder /work/handline.architect' },
    { kind: 'dialog', key: ['claude', 'Do you trust the files', 'Yes, I trust'], text: 'claude: Do you trust the files → Yes, I trust' },
  ];

  /** `on` false is a daemon that does not serve stats, settings or standing answers: it lists none of their methods. */
  constructor(now: () => Timestamp, on: boolean) {
    this.now = now;
    this.on = on;
  }

  /** Whether the daemon lists this method. */
  lists = (name: string): boolean => this.on || !ADMIN.test(name);

  /** Whether this class answers the method. */
  owns = (name: string): boolean => ADMIN.test(name);

  call(method: string, p: Record<string, unknown>, accepted: Accepted): unknown {
    switch (method) {
      case 'stats.get':
        return this.stats(p.window === '7d' ? '7d' : '24h');
      case 'stats.outlook':
        return this.outlook(String(p.range) as Outlook['range'], p.agent);
      case 'settings.get':
        return this.settings();
      case 'settings.set':
        return (this.set((p.changes ?? {}) as SettingsChanges), accepted('Saved.'));
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
      ...(pick !== undefined && { overlay: (OVERLAYS[pick] ?? []).map((v, i) => (i < past.length ? v : 0)).slice(0, past.length) }),
    };
  }

  settings(): Settings {
    return {
      timings: {},
      terminal_host: { choice: 'fake', available: ['fake'], health: 'ok' },
      trust_folders: [],
      hooks: { installed: true, accounts: [] },
      values: { ...this.values },
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
        if (key in DEFAULTS) values[key] = DEFAULTS[key as keyof typeof DEFAULTS];
        else delete values[key];
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
