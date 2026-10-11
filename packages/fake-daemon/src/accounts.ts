// The fake daemon's accounts: gauges, who is on each, and the settings the page edits, on the accounts topic.
import type { Account, Timestamp } from '@handline/contract';
import { Failure, invalidParams } from './errors.ts';

/** The code a test daemon's login takes as right; any other ends the login failed. */
export const GOOD_LOGIN_CODE = 'good-code';
const VENDORS = ['claude', 'opencode'];

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
    // The topic row is the one without the usage fields, as the gateway sends it.
    const { plan: _p, assignable: _a, assign_reason: _r, ...row } = a;
    this.host.emit('accounts', 'account.changed', { title: a.label, data: row });
  }
  private agentsOn(account: string): string[] {
    return [...this.host.sessions.values()].filter((s) => s.fx.account === account).map((s) => s.fx.name);
  }

  /** Adds an account; its `agents` are read from the sessions when listed. */
  add(a: Partial<Account> & { name: string }): Account {
    const row: Account = {
      login_state: 'ok',
      login: null,
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
      plan: 'pro',
      assignable: true,
      assign_reason: '',
    });
    this.add({
      name: 'beta',
      label: 'review',
      five_hour: { used_pct: 88, resets_at: at(1), source: 'measured' },
      weekly: { used_pct: 30, resets_at: at(120), source: 'estimated' },
      cycle: { on: true, end: '2026-10-28', end_is_default: false },
      plan: 'pro',
      assignable: false,
      assign_reason: '5h 88% at last reading',
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
      case 'account.add':
        return this.register(p, accepted);
      case 'account.login':
        return this.startLogin(p, accepted);
      case 'account.login_code':
        return (this.giveCode(p), accepted('Code sent.'));
      case 'account.login_cancel':
        return (this.cancelLogin(p), accepted('Cancelled.'));
      case 'account.remove':
        return this.remove(p, accepted);
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

  /** `hl account add --no-login`: the account exists and needs a login. A used name is refused; a bad name or an unknown vendor is a bad param. */
  private register(p: Record<string, unknown>, accepted: (said: string, extra?: object) => unknown): unknown {
    const name = p.account;
    if (typeof name !== 'string' || !name || name.includes('/') || name.startsWith('.')) throw invalidParams('bad account name', 'account');
    if (p.vendor !== undefined && !VENDORS.includes(String(p.vendor))) throw invalidParams(`no driver for ${String(p.vendor)}`, 'vendor');
    if (this.rows.some((x) => x.name === name))
      throw new Failure('refused', `account '${name}' already exists`, { reason: 'exists', gate: 'state' });
    this.emit(this.view(this.add({ name, login_state: 'needs_login' })));
    return accepted('Added.', { account: name });
  }

  private logins = new Map<string, Account>();
  private loginSeq = 0;

  /** A login runs without a terminal: the reply holds the URL, and the code comes back through `giveCode`. One at a time per account. */
  private startLogin(p: Record<string, unknown>, accepted: (said: string, extra?: object) => unknown): unknown {
    const r = this.find(p.account);
    if (r.login && (r.login.state === 'waiting' || r.login.state === 'code_sent'))
      throw new Failure('refused', `${r.name} is already logging in`, { reason: 'logging_in', gate: 'state' });
    const login_id = `login-${++this.loginSeq}`;
    this.logins.set(login_id, r);
    this.setLogin(r, login_id, 'waiting');
    return accepted('Login started.', { login_id, state: 'waiting', url: `https://login.example/authorize?login=${login_id}` });
  }

  private setLogin(r: Account, login_id: string, state: NonNullable<Account['login']>['state'], reason?: string): void {
    r.login = reason ? { login_id, state, reason } : { login_id, state };
    if (state === 'done') r.login_state = 'ok';
    this.emit(this.view(r));
  }
  private login(id: unknown): Account {
    const r = this.logins.get(String(id));
    if (!r || r.login?.login_id !== id) throw new Failure('not_found', `no login ${String(id)}`, { what: 'login' });
    return r;
  }

  /** The code goes to a login that waits for it; the login ends at once, done for the right code and failed with a reason for any other. */
  private giveCode(p: Record<string, unknown>): void {
    const r = this.login(p.login_id);
    if (typeof p.code !== 'string' || !p.code) throw invalidParams('a code is needed', 'code');
    if (r.login!.state !== 'waiting')
      throw new Failure('refused', 'the login is not waiting for a code', { reason: 'not_waiting', gate: 'state' });
    this.setLogin(r, String(p.login_id), 'code_sent');
    if (p.code === GOOD_LOGIN_CODE) this.setLogin(r, String(p.login_id), 'done');
    else this.setLogin(r, String(p.login_id), 'failed', 'the code was not accepted');
  }

  /** Idempotent: a login that has ended stays as it ended. */
  private cancelLogin(p: Record<string, unknown>): void {
    const r = this.login(p.login_id);
    if (r.login!.state === 'waiting' || r.login!.state === 'code_sent') this.setLogin(r, String(p.login_id), 'cancelled');
  }

  /** The sequence of `hl account remove`: refused while an agent is on the account; a dry run says so and changes nothing; else `confirm` is the name. */
  private remove(p: Record<string, unknown>, accepted: (said: string, extra?: object) => unknown): unknown {
    const r = this.find(p.account);
    const on = this.agentsOn(r.name);
    const plan = on.length
      ? [`refused: ${on.join(', ')} still on ${r.name}`]
      : [`log ${r.name} out`, `move its directory aside`, `take ${r.name} off the roster`];
    if (p.dry_run) return accepted('Dry run.', { would_remove: !on.length, plan });
    if (on.length) throw new Failure('refused', plan[0]!, { reason: 'agents_on_account', gate: 'state' });
    if (p.confirm !== r.name) throw new Failure('bad_request', `confirm must be the account's name, ${r.name}`);
    this.rows = this.rows.filter((x) => x !== r);
    return accepted('Removed.');
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
    if (!r) throw new Failure('not_found', `no account ${String(name)}`, { what: 'account' });
    return r;
  }
}
