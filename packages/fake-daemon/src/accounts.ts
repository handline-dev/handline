// The fake daemon's accounts: gauges, who is on each, and the settings the page edits, on the accounts topic.
import type { Account, Timestamp } from '@handline/contract';
import { Failure } from './errors.ts';

/** What the accounts need of the daemon that hosts them: to emit on their topic, and the agents' fixtures. */
interface Host {
  emit(topic: 'accounts', type: string, f: { title?: string; data: Record<string, unknown> }): void;
  sessions: Map<string, { fx: { name: string; account: string | null } }>;
}
const HOUR = 3_600_000;

export class FakeAccounts {
  private rows: Account[] = [];
  private host: Host;
  private now: () => Timestamp;
  private on: boolean;

  /** `on` false is a daemon that does not serve accounts: it lists none of their methods. */
  constructor(host: Host, now: () => Timestamp, on: boolean) {
    this.host = host;
    this.now = now;
    this.on = on;
    if (on) this.seed();
  }

  /** Whether the daemon lists this method. */
  lists = (name: string): boolean => this.on || !/^accounts?\./.test(name);

  private emit(a: Account) {
    this.host.emit('accounts', 'account.changed', { title: a.label, data: a as unknown as Record<string, unknown> });
  }
  private agentsOn(account: string): string[] {
    return [...this.host.sessions.values()].filter((s) => s.fx.account === account).map((s) => s.fx.name);
  }

  /** Adds an account; its `agents` are read from the sessions when listed. */
  add(a: Partial<Account> & { name: string }): Account {
    const row: Account = {
      login_state: 'ok',
      five_hour: null,
      weekly: null,
      agents: [],
      moves: [],
      label: a.name,
      cap: null,
      cap_effective: 4,
      recover: false,
      cycle: { on: false, end: null, end_is_default: true },
      ...a,
    };
    this.rows.push(row);
    return row;
  }

  /** Seeds the three accounts the sessions' fixtures mention, in the states a page has to draw. */
  private seed(): void {
    const at = (h: number): Timestamp => new Date(Date.parse(this.now()) + h * HOUR).toISOString();
    this.add({
      name: 'alpha',
      five_hour: { used_pct: 41, resets_at: at(2.2), source: 'measured' },
      weekly: { used_pct: 62, resets_at: at(70), source: 'measured' },
      cap: 4,
      label: 'alpha',
    });
    this.add({
      name: 'beta',
      label: 'review',
      five_hour: { used_pct: 88, resets_at: at(1), source: 'measured' },
      weekly: { used_pct: 30, resets_at: at(120), source: 'estimated' },
      cycle: { on: true, end: '2026-10-28', end_is_default: false },
    });
    this.add({ name: 'gamma', login_state: 'needs_login', five_hour: null, weekly: null });
  }

  /** One accounts method; `accepted` makes the reply of a write. */
  call(method: string, p: Record<string, unknown>, accepted: (said: string, extra?: object) => unknown): unknown {
    switch (method) {
      case 'accounts.list':
        return this.list();
      case 'account.set':
        return (this.set(p), accepted('Saved.'));
      case 'account.set_cap':
        return (this.set({ account: p.account, cap: p.cap }), accepted('Saved.'));
      case 'account.login':
        return accepted('Login started.', { terminal: this.login(p) });
      default:
        throw new Failure('refused', `the fake daemon does not serve ${method}`, { reason: 'unsupported', gate: 'unsupported' });
    }
  }

  list(): { accounts: Account[] } {
    return { accounts: this.rows.map((r) => this.view(r)) };
  }

  set(p: Record<string, unknown>): void {
    const r = this.find(p.account);
    if (typeof p.label === 'string') r.label = p.label || r.name;
    if (typeof p.cap === 'number') {
      r.cap = p.cap;
      r.cap_effective = p.cap;
    }
    if (typeof p.recover === 'boolean') r.recover = p.recover;
    if (typeof p.cycle === 'boolean') r.cycle = { ...r.cycle, on: p.cycle };
    if (p.cycle_end !== undefined)
      r.cycle = {
        on: p.cycle_end !== null || r.cycle.on,
        end: (p.cycle_end as string | null) ?? null,
        end_is_default: p.cycle_end === null,
      };
    this.emit(this.view(r));
  }

  /** A login opens a terminal for the vendor's own flow; the account is `needs_login` until the terminal ends (a test calls `loggedIn`). */
  login(p: Record<string, unknown>): string {
    this.find(p.account);
    return `term:login:${String(p.account)}`;
  }

  loggedIn(name: string): void {
    const r = this.find(name);
    r.login_state = 'ok';
    this.emit(this.view(r));
  }

  private view(r: Account): Account {
    return { ...r, agents: this.agentsOn(r.name), cycle: { ...r.cycle } };
  }
  private find(name: unknown): Account {
    const r = this.rows.find((x) => x.name === name);
    if (!r) throw new Failure('not_found', `no account ${String(name)}`);
    return r;
  }
}
