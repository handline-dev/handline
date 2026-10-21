// The fake daemon's view preferences: one set for the founder's devices, as prefs.get and prefs.set describe.
import type { NavPrefs, NavPrefsChanges, Prefs, Topic } from '@handline/contract';
import { badRequest } from '#src/errors.ts';

const LAYOUTS: NavPrefs['layout'][] = ['tree', 'flat'];
const GROUPS: NavPrefs['hidden'] = ['needs', 'working', 'waking', 'idle', 'sleeping'];

export interface PrefsHost {
  sessions: ReadonlyMap<string, unknown>;
  emit(topic: Topic, type: string, f: { data: Record<string, unknown> }): void;
}

export class FakePrefs {
  private nav: NavPrefs = { pins: [], unread_only: false, hidden: [], layout: 'tree', collapsed: [] };
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

  /** A retired agent leaves the pins and the folds. */
  forget(agent: string): void {
    const drop = (names: string[]) => names.filter((n) => n !== agent);
    if (!this.nav.pins.includes(agent) && !this.nav.collapsed.includes(agent)) return;
    this.store({ pins: drop(this.nav.pins), collapsed: drop(this.nav.collapsed) });
  }

  /** Every key is checked as the contract types it; a pin or a fold must name a known agent, once. */
  private set(nav: NavPrefsChanges | undefined): void {
    if (!nav || typeof nav !== 'object' || Array.isArray(nav)) throw badRequest('changes.nav is required', 'changes');
    const extra = Object.keys(nav).find((k) => !(k in this.nav));
    if (extra) throw badRequest(`no pref named ${extra}`, extra);
    if (nav.unread_only !== undefined && typeof nav.unread_only !== 'boolean') throw badRequest('unread_only is a boolean', 'unread_only');
    if (nav.layout !== undefined && !LAYOUTS.includes(nav.layout)) throw badRequest(`no layout ${nav.layout}`, 'layout');
    if (nav.hidden?.some((g) => !GROUPS.includes(g))) throw badRequest('hidden names an unknown group', 'hidden');
    for (const field of ['pins', 'collapsed'] as const) {
      const names = nav[field] ?? [];
      if (new Set(names).size !== names.length) throw badRequest(`${field} names an agent twice`, field);
      const unknown = names.find((n) => !this.host.sessions.has(n));
      if (unknown) throw badRequest(`no agent named ${unknown}`, field);
    }
    this.store(nav);
  }

  /** The change goes to every device on the prefs topic. */
  private store(nav: NavPrefsChanges): void {
    this.nav = { ...this.nav, ...nav };
    this.host.emit('prefs', 'prefs.changed', { data: this.get() as unknown as Record<string, unknown> });
  }
}
