// The fake daemon's read-mostly admin surface: the stats windows, the usage outlook, the home settings, the standing answers, and the repos
// and worktrees (worktrees.ts).
import type { ConnectionInfo, Outlook, MachineNeed, Settings, SettingsChanges, StandingAnswer, Timestamp } from '@handline/contract';
import { Failure, badRequest } from '#src/errors.ts';
import { AGENTS, outlookOf, statsOf } from '#src/usage.ts';
import { FakeWorktrees } from '#src/worktrees.ts';
import { FakePrefs, type PrefsHost } from '#src/prefs.ts';

type Accepted = (said: string) => unknown;
const FINGERPRINT = 'sha256:3f9c1e7a52b84d06a1e3c7f90b2d4856e1a7c3095bd8f2e46a0c5d71b93e8f24';

/** The methods this answers. */
const ADMIN = /^(stats\.|connection\.get|settings\.|answers?\.|devices?\.(list|revoke)|agent\.triggers|memory\.status|events\.feed)/;
/** The built-in handoff prompt (go/internal/handoff/note.go AskTemplate). */
const BUILTIN_HANDOFF =
  "Write your handoff to {path} now, while your prompt cache is warm, with one Write (replace what is there), then stop and run nothing else. The next session of you, if it starts fresh, reads it first: copy yourself into it. At most {max} characters, about 30 lines, only what `hl` and your memory cannot tell it: (1) what you were deciding and why; (2) what you meant to do next; (3) promises or doubts not yet on any task; (4) the founder's preferences and the pitfalls you learned this session; (5) who you wait on, as T<n>/#<n> numbers only; (6) what slowed you down: one line each, starting with 'Slowed:', naming the command, tool, rule or T number and what it cost (retries, a wait, a refusal), or 'Slowed: none' if nothing did. Do not restate what a task or message holds; cite its T or # number. A lasting lesson or preference goes to your memory first, not here. If there is nothing, write the single word: nothing.";
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
  handoff_prompt: BUILTIN_HANDOFF,
  push_prompt_asked: true,
  push_task_done: true,
  push_stall: true,
  push_account_out: true,
  pacing: true,
  pace_target_pct: 98,
  pace_burst_pct: 10,
  host_active_cap: 0,
  load_per_core_pct: 150,
  ram_free_min_pct: 10,
  host_simulator_hold: 1,
  heavy_slots: 2,
  keep_awake: 'off',
  reap_orphans: 'dry',
};

export class FakeAdmin {
  readonly worktrees: FakeWorktrees;
  readonly prefs: FakePrefs;
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

  /** The machine's own needs-you items in daemon.status (a full disk); `setMachineNeeds` of helpers.ts changes them. */
  machineNeeds: MachineNeed[] = [];

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
        return statsOf(this.now(), p.window === '7d' ? '7d' : '24h');
      case 'stats.outlook':
        return outlookOf(this.now(), String(p.range) as Outlook['range'], p.agent);
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

  /** Answer connection.get as a process with no network listener (a daemon behind a gateway). */
  noListener = false;

  /** A TLS listener on a Tailscale-less address, one heavy slot, dev mode as the settings say. */
  connection(): ConnectionInfo {
    return {
      served_by: this.noListener ? 'gateway' : 'daemon',
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
      handoff_prompt_default: BUILTIN_HANDOFF,
      handoff_prompt_own: [...this.handoffOwn],
    } as unknown as Settings;
  }

  /** Agents whose own agent.yaml sets a handoff prompt, which wins over this one. */
  handoffOwn: string[] = [];

  set(changes: SettingsChanges): void {
    // As the real daemon does: a key the contract does not list is refused, so an app writing a key nobody listed fails its test.
    for (const key of Object.keys(changes)) if (!(key in DEFAULTS)) throw badRequest(`${key} is not a setting`, key);
    for (const [key, given] of Object.entries(changes)) {
      let value = given;
      if (key === 'handoff_prompt' && typeof value === 'string') {
        value = value.replace(/\s*[\r\n]+\s*/g, ' ').trim();
        if (value && value.length < 20) throw badRequest('Too short to guide a note. Leave it blank for the built-in.', 'handoff_prompt');
        if (!value) value = null;
      }
      if (key === 'active_cap' && value !== null && (!Number.isInteger(value) || (value as number) < 1))
        throw badRequest('active_cap is a whole number from 1', 'active_cap');
      if (key === 'park_timeout_s' && value !== null && (!Number.isInteger(value) || (value as number) < 0))
        throw badRequest('park_timeout_s is a whole number of seconds, 0 for never', 'park_timeout_s');
      if (key === 'heavy_slots' && value !== null && (!Number.isInteger(value) || (value as number) < 1))
        throw badRequest('heavy_slots is a whole number, at least 1', 'heavy_slots');
      if (key === 'keep_awake' && value !== null && value !== 'off' && value !== 'on' && value !== 'working')
        throw badRequest('keep_awake is off, on or working', 'keep_awake');
      if (key === 'reap_orphans' && value !== null && value !== 'dry' && value !== 'on' && value !== 'off')
        throw badRequest('reap_orphans is dry, on or off', 'reap_orphans');
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
