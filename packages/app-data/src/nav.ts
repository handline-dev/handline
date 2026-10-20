// The agent list's and the dock's state: the person's prefs, kept by the daemon and the same on every device, and the agents opened on
// this device since the app started, which no other device sees.
import { useRef, useSyncExternalStore } from 'react';
import { movePin, navOf, togglePin, withOpen } from '@handline/app-core';
import { useQueryClient } from '@tanstack/react-query';
import type { NavPrefs, NavPrefsChanges, Prefs } from '@handline/client';
import { useData } from '#src/provider.ts';
import { usePrefs } from '#src/read.ts';
import { qk } from '#src/reads.ts';
import { useWrite } from '#src/write.ts';

/**
 * The nav prefs (the defaults until they load, or when the daemon does not serve them) and the ways to change them. Nothing is sent before
 * they load: a key is replaced whole, so a pin made from the defaults would drop the stored ones. A pin change starts from the one still
 * on its way, so two in one tick both count.
 */
export function useNav(): {
  nav: NavPrefs;
  ready: boolean;
  change: (changes: NavPrefsChanges) => void;
  togglePin: (agent: string) => void;
  movePin: (agent: string, index: number) => void;
} {
  const prefs = usePrefs();
  const qc = useQueryClient();
  const { daemon } = useData();
  const set = useWrite('prefs.set');
  const ready = prefs.data !== undefined;
  const sent = useRef<string[] | null>(null);
  const change = (changes: NavPrefsChanges) => {
    if (ready) set.mutate({ changes: { nav: changes } });
  };
  const pin = (next: (pins: string[]) => string[]) => {
    if (!ready) return;
    const pins = next(sent.current ?? navOf(qc.getQueryData<Prefs>(qk.prefs(daemon))).pins);
    sent.current = pins;
    set.mutate(
      { changes: { nav: { pins } } },
      {
        onSettled: () => {
          if (sent.current === pins) sent.current = null;
        },
      },
    );
  };
  return {
    nav: navOf(prefs.data),
    ready,
    change,
    togglePin: (agent) => pin((p) => togglePin(p, agent)),
    movePin: (agent, index) => pin((p) => movePin(p, agent, index)),
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
