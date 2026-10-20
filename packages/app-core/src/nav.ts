// Which agents the agent list (every app) and the phone's dock show: the person's pins, filter, layout and folds come from the daemon
// (prefs.get); the agents opened on this device are the app's own. Pure.
import type { AgentView, NavPrefs, Prefs } from '@handline/client';
import { byUrgency, railMatch, railRows, visibleRows, RAIL_FILTERS, type Dot, type RailFilter, type RailRow } from '#src/model.ts';

export type NavGroup = NavPrefs['hidden'][number];
export type NavChip = 'all' | 'unread' | 'needs';

export const DEFAULT_NAV: NavPrefs = { pins: [], unread_only: false, hidden: [], layout: 'tree', collapsed: [] };
/** How many agents opened on this device the dock keeps beside the pinned ones. */
export const OPEN_MAX = 5;

export const navOf = (p: Prefs | undefined): NavPrefs => ({ ...DEFAULT_NAV, ...p?.nav });

const OTHER_GROUPS = RAIL_FILTERS.map((f) => f.id as NavGroup).filter((g) => g !== 'needs');

/** The dots the hidden groups cover. */
export const hiddenDots = (hidden: readonly NavGroup[]): Set<Dot> =>
  new Set(RAIL_FILTERS.filter((f) => hidden.includes(f.id as NavGroup)).flatMap((f) => f.dots));

/** The chip the filter reads as, or null when it was set some other way (the PC's group menu). */
export function chipOf(nav: NavPrefs): NavChip | null {
  if (nav.unread_only) return nav.hidden.length ? null : 'unread';
  if (!nav.hidden.length) return 'all';
  const needsOnly = nav.hidden.length === OTHER_GROUPS.length && OTHER_GROUPS.every((g) => nav.hidden.includes(g));
  return needsOnly ? 'needs' : null;
}

/** The filter a chip sets, as prefs.set changes. */
export const chipChanges = (chip: NavChip): Pick<NavPrefs, 'unread_only' | 'hidden'> => ({
  unread_only: chip === 'unread',
  hidden: chip === 'needs' ? [...OTHER_GROUPS] : [],
});

const filterOf = (nav: Pick<NavPrefs, 'unread_only' | 'hidden'>, search = ''): RailFilter => ({
  hidden: hiddenDots(nav.hidden),
  unread: nav.unread_only,
  search,
});

/** How many agents each chip lets through on their own (a tree also shows their parents). */
export function chipCounts(agents: readonly AgentView[]): Record<NavChip, number> {
  const count = (chip: NavChip) => agents.filter(railMatch(filterOf(chipChanges(chip)))).length;
  return { all: count('all'), unread: count('unread'), needs: count('needs') };
}

/** The pinned agents in the person's order; a pin naming an agent that is gone is skipped. */
export function pinnedOf(agents: readonly AgentView[], pins: readonly string[]): AgentView[] {
  const byName = new Map(agents.map((a) => [a.name, a]));
  return pins.flatMap((n) => byName.get(n) ?? []);
}

/** The dock: the pinned agents, then the ones opened on this device that are not pinned. */
export function navDock(
  agents: readonly AgentView[],
  pins: readonly string[],
  open: readonly string[],
): { pinned: AgentView[]; open: AgentView[] } {
  return {
    pinned: pinnedOf(agents, pins),
    open: pinnedOf(
      agents,
      open.filter((n) => !pins.includes(n)),
    ),
  };
}

/**
 * The agent list: the pinned agents on top whatever the filter, then every agent the filter and search let through (a pinned one too),
 * as a folded tree (railRows) or flat by urgency.
 */
export function navList(agents: AgentView[], nav: NavPrefs, search = ''): { pinned: AgentView[]; rows: RailRow[] } {
  const filter = filterOf(nav, search);
  return {
    pinned: pinnedOf(agents, nav.pins),
    rows:
      nav.layout === 'flat'
        ? agents
            .filter(railMatch(filter))
            .sort(byUrgency)
            .map((agent) => ({ agent, depth: 0, kids: 0 }))
        : visibleRows(railRows(agents, filter), new Set(nav.collapsed)),
  };
}

/** The pins with `name` added at the end, or taken out. */
export const togglePin = (pins: readonly string[], name: string): string[] =>
  pins.includes(name) ? pins.filter((n) => n !== name) : [...pins, name];

/** The pins with `name` moved to `index`. */
export function movePin(pins: readonly string[], name: string, index: number): string[] {
  const rest = pins.filter((n) => n !== name);
  if (rest.length === pins.length) return [...pins];
  rest.splice(Math.max(0, Math.min(index, rest.length)), 0, name);
  return rest;
}

/** This device's open agents after opening `name`: one already open keeps its place; past `max` the oldest goes. */
export function withOpen(open: readonly string[], name: string, max = OPEN_MAX): string[] {
  if (open.includes(name)) return [...open];
  return [...open, name].slice(-max);
}
