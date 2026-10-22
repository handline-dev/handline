// The fake daemon's view preferences: one set for the founder's devices, as prefs.get and prefs.set describe.
import type { NavPrefs, NavPrefsChanges, Prefs, Topic } from '@handline/contract';
import { invalidParams } from '#src/errors.ts';

const LAYOUTS: NavPrefs['layout'][] = ['tree', 'flat'];
const GROUPS: NavPrefs['hidden'] = ['needs', 'working', 'waking', 'idle', 'sleeping'];

export interface PrefsHost {
  sessions: ReadonlyMap<string, unknown>;
  emit(topic: Topic, type: string, f: { data: Record<string, unknown> }): void;
}

export class FakePrefs {
  private nav: NavPrefs = { pins: [], unread_only: false, hidden: [], layout: 'tree', collapsed: [], order: [] };
  private host: PrefsHost;

  constructor(host: PrefsHost) {
    this.host = host;
  }

  owns = (method: string) => method === 'prefs.get' || method === 'prefs.set';

  call(method: string, p: Record<string, unknown>, ok: (said: string) => unknown): unknown {
    if (method === 'prefs.get') return this.get();
    this.set((p.changes as { nav?: NavPrefsChanges } | undefined)?.nav);
    return ok('Saved.');
  }

  private get = (): Prefs => ({ nav: { ...this.nav } });

  /** A retired agent leaves the pins, the folds and the order. */
  forget(agent: string): void {
    const drop = (names: string[]) => names.filter((n) => n !== agent);
    const { pins, collapsed, order } = this.nav;
    if (![pins, collapsed, order].some((names) => names.includes(agent))) return;
    this.store({ pins: drop(pins), collapsed: drop(collapsed), order: drop(order) });
  }

  /** Every key is checked as the contract types it; a pin, a fold or an order entry must name a known agent, once. */
  private set(nav: NavPrefsChanges | undefined): void {
    if (!nav || typeof nav !== 'object' || Array.isArray(nav)) throw invalidParams('changes.nav is required', 'changes.nav');
    const extra = Object.keys(nav).find((k) => !(k in this.nav));
    if (extra) throw invalidParams(`no pref named ${extra}`, `changes.nav.${extra}`);
    if (nav.unread_only !== undefined && typeof nav.unread_only !== 'boolean')
      throw invalidParams('unread_only is a boolean', 'changes.nav.unread_only');
    if (nav.layout !== undefined && !LAYOUTS.includes(nav.layout)) throw invalidParams(`no layout ${nav.layout}`, 'changes.nav.layout');
    if (nav.hidden?.some((g) => !GROUPS.includes(g))) throw invalidParams('hidden names an unknown group', 'changes.nav.hidden');
    for (const field of ['pins', 'collapsed', 'order'] as const) {
      const names = nav[field] ?? [];
      if (!Array.isArray(names)) throw invalidParams(`${field} is a list of agent names`, `changes.nav.${field}`);
      if (new Set(names).size !== names.length) throw invalidParams(`${field} names an agent twice`, `changes.nav.${field}`);
      const unknown = names.find((n) => !this.host.sessions.has(n));
      if (unknown) throw invalidParams(`no agent named ${unknown}`, `changes.nav.${field}`);
    }
    this.store(nav);
  }

  /** The change goes to every device on the prefs topic. */
  private store(nav: NavPrefsChanges): void {
    this.nav = { ...this.nav, ...nav };
    this.host.emit('prefs', 'prefs.changed', { data: this.get() as unknown as Record<string, unknown> });
  }
}
