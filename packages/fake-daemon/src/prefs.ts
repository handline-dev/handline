// The fake daemon's view preferences: one set for the founder's devices, as prefs.get and prefs.set describe.
import type { NavPrefs, NavPrefsChanges, Prefs, Topic } from '@handline/contract';
import { badRequest } from '#src/errors.ts';

interface Host {
  sessions: ReadonlyMap<string, unknown>;
  emit(topic: Topic, type: string, f: { data: Record<string, unknown> }): void;
}

export class FakePrefs {
  private nav: NavPrefs = { pins: [], unread_only: false, hidden: [], layout: 'tree', collapsed: [] };
  private host: Host;

  constructor(host: Host) {
    this.host = host;
  }

  owns = (method: string) => method === 'prefs.get' || method === 'prefs.set';

  call(method: string, p: Record<string, unknown>, ok: (said: string) => object): object {
    if (method === 'prefs.get') return this.get();
    this.set((p.changes as { nav?: NavPrefsChanges } | undefined)?.nav);
    return ok('Saved.');
  }

  private get = (): Prefs => ({ nav: { ...this.nav } });

  /** A pin or a fold must name a known agent, once; the change goes to every device on the prefs topic. */
  private set(nav: NavPrefsChanges | undefined): void {
    if (!nav || typeof nav !== 'object') throw badRequest('changes.nav is required', 'changes');
    for (const field of ['pins', 'collapsed'] as const) {
      const names = nav[field] ?? [];
      if (new Set(names).size !== names.length) throw badRequest(`${field} names an agent twice`, field);
      const unknown = names.find((n) => !this.host.sessions.has(n));
      if (unknown) throw badRequest(`no agent named ${unknown}`, field);
    }
    this.nav = { ...this.nav, ...nav };
    this.host.emit('prefs', 'prefs.changed', { data: this.get() as unknown as Record<string, unknown> });
  }
}
