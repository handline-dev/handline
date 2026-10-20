// The agent list's and the dock's state: the person's prefs, kept by the daemon and the same on every device, and the agents opened on
// this device since the app started, which no other device sees.
import { useSyncExternalStore } from 'react';
import { movePin, navOf, togglePin, withOpen } from '@handline/app-core';
import type { NavPrefs, NavPrefsChanges } from '@handline/client';
import { useData, useServes } from '#src/provider.ts';
import { usePrefs } from '#src/read.ts';
import { useWrite } from '#src/write.ts';

/** The nav prefs (the defaults until they load, or when the daemon does not serve them) and the ways to change them. */
export function useNav(): {
  nav: NavPrefs;
  served: boolean;
  change: (changes: NavPrefsChanges) => void;
  togglePin: (agent: string) => void;
  movePin: (agent: string, index: number) => void;
} {
  const prefs = usePrefs();
  const { served } = useServes('prefs.set');
  const set = useWrite('prefs.set');
  const nav = navOf(prefs.data);
  const change = (changes: NavPrefsChanges) => {
    if (served) set.mutate({ changes: { nav: changes } });
  };
  return {
    nav,
    served,
    change,
    togglePin: (agent) => change({ pins: togglePin(nav.pins, agent) }),
    movePin: (agent, index) => change({ pins: movePin(nav.pins, agent, index) }),
  };
}

const opened = new Map<string, readonly string[]>();
const listeners = new Set<() => void>();
const NONE: readonly string[] = [];
const subscribe = (f: () => void) => (listeners.add(f), () => void listeners.delete(f));
const put = (daemon: string, f: (tabs: readonly string[]) => readonly string[]) => {
  opened.set(daemon, f(opened.get(daemon) ?? NONE));
  for (const l of listeners) l();
};

/** The agents opened on this device since the app started, oldest first, at most OPEN_MAX. */
export function useOpenTabs(): { tabs: readonly string[]; open: (agent: string) => void; close: (agent: string) => void } {
  const { daemon } = useData();
  const tabs = useSyncExternalStore(subscribe, () => opened.get(daemon) ?? NONE);
  return {
    tabs,
    open: (agent) => put(daemon, (t) => withOpen(t, agent)),
    close: (agent) => put(daemon, (t) => t.filter((n) => n !== agent)),
  };
}
