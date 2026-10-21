// Which agents the agent list (every app) and the phone's dock show, from the person's prefs and this device's open tabs. Pure.
import type { AgentView, NavPrefs, Prefs, Worktree } from '@handline/client';
import { byUrgency, railMatch, railRows, visibleRows, RAIL_FILTERS, type Dot, type RailFilter, type RailRow } from '#src/model.ts';

export type NavGroup = NavPrefs['hidden'][number];
export type NavChip = 'all' | 'unread' | 'needs';

export const DEFAULT_NAV: NavPrefs = { pins: [], unread_only: false, hidden: [], layout: 'tree', collapsed: [] };
/** How many open tabs, not counting pinned agents, the dock keeps. */
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

/** An agent opened on this device, and the worktree it shows (absent: the one the agent owns). */
export interface OpenTab {
  agent: string;
  path?: string;
}
export interface DockItem {
  agent: AgentView;
  branch: string | null;
}

/** The branch of the worktree at `path`, else of the first one the agent owns. */
export const branchOf = (worktrees: readonly Worktree[], agent: string, path?: string): string | null =>
  worktrees.find((w) => (path ? w.path === path : w.owner === agent))?.branch ?? null;

/** The dock: the pinned agents, then the open tabs that are not pinned, each with its branch. */
export function navDock(
  agents: readonly AgentView[],
  pins: readonly string[],
  open: readonly OpenTab[],
  worktrees: readonly Worktree[],
): { pinned: DockItem[]; open: DockItem[] } {
  const item = (a: AgentView): DockItem => ({ agent: a, branch: branchOf(worktrees, a.name, open.find((t) => t.agent === a.name)?.path) });
  const unpinned = open.map((t) => t.agent).filter((n) => !pins.includes(n));
  return { pinned: pinnedOf(agents, pins).map(item), open: pinnedOf(agents, unpinned).map(item) };
}

/** The agent list: pinned agents on top, then what the filter lets through as a folded tree or flat by urgency. */
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

/** The open tabs after opening `tab`: an open agent keeps its place and takes the new path; past `max` unpinned, the oldest unpinned goes. */
export function withOpen(open: readonly OpenTab[], tab: OpenTab, pins: readonly string[] = [], max = OPEN_MAX): OpenTab[] {
  if (open.some((t) => t.agent === tab.agent)) return open.map((t) => (t.agent === tab.agent ? tab : t));
  const next = [...open, tab];
  const unpinned = next.filter((t) => !pins.includes(t.agent));
  const drop = new Set(unpinned.slice(0, Math.max(0, unpinned.length - max)));
  return next.filter((t) => !drop.has(t));
}
