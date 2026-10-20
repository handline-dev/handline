// The person's nav prefs (the daemon's, on every device) and this device's open tabs (this app run only).
import { useRef, useSyncExternalStore } from 'react';
import { movePin, navDock, navOf, togglePin, withOpen, type DockItem, type OpenTab } from '@handline/app-core';
import { useQueryClient } from '@tanstack/react-query';
import type { NavPrefs, NavPrefsChanges, Prefs } from '@handline/client';
import { useData } from '#src/provider.ts';
import { useAgents, usePrefs, useWorktrees } from '#src/read.ts';
import { qk } from '#src/reads.ts';
import { useWrite } from '#src/write.ts';

/** The nav prefs, the defaults until they load; nothing is sent before then, since a key is replaced whole. */
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
  const sent = useRef<string[] | null>(null); // the pins still on their way, so two changes in one tick both count
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

const opened = new Map<string, readonly OpenTab[]>();
const listeners = new Set<() => void>();
const NONE: readonly OpenTab[] = [];
const subscribe = (f: () => void) => (listeners.add(f), () => void listeners.delete(f));
const put = (daemon: string, f: (tabs: readonly OpenTab[]) => readonly OpenTab[]) => {
  opened.set(daemon, f(opened.get(daemon) ?? NONE));
  for (const l of listeners) l();
};

/** The agents opened on this device, oldest first, each with the worktree it shows; at most OPEN_MAX unpinned. */
export function useOpenTabs(): { tabs: readonly OpenTab[]; open: (agent: string, path?: string) => void; close: (agent: string) => void } {
  const { daemon } = useData();
  const qc = useQueryClient();
  const tabs = useSyncExternalStore(subscribe, () => opened.get(daemon) ?? NONE);
  const pins = () => navOf(qc.getQueryData<Prefs>(qk.prefs(daemon))).pins;
  return {
    tabs,
    open: (agent, path) => put(daemon, (t) => withOpen(t, { agent, ...(path && { path }) }, pins())),
    close: (agent) => put(daemon, (t) => t.filter((x) => x.agent !== agent)),
  };
}

/** The phone's dock: pinned agents, then open tabs, each with its branch. */
export function useNavDock(): { pinned: DockItem[]; open: DockItem[] } {
  const agents = useAgents().data?.agents ?? [];
  const worktrees = useWorktrees().data?.worktrees ?? [];
  return navDock(agents, useNav().nav.pins, useOpenTabs().tabs, worktrees);
}
