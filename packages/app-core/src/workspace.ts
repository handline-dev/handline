// The pane workspace of docs/web-main-view.md §4: tab groups in a split tree. Data and pure functions only (no React, no DOM),
// so web and mobile share it and Node tests drive it. A refused move returns null; every other function returns the next workspace.
import { breakpoints } from '@handline/tokens';
import type { KV } from './model.ts';
import { detach, group, groupOf, groupsOf, replaceGroup } from './layout-tree.ts';

export { groupOf, groupsOf };

export type TabKind = 'chat' | 'terminal' | 'shell' | 'emulator' | 'web' | 'markdown' | 'review';
export const TAB_KINDS: readonly TabKind[] = ['chat', 'terminal', 'shell', 'emulator', 'web', 'markdown', 'review'];
export type TabParams = Record<string, string | number>;
export interface Tab {
  id: string;
  kind: TabKind;
  params: TabParams;
}
/** A pane: its own tab strip. Never empty, so `activeTab` always names one of `tabs`. */
export interface Group {
  type: 'group';
  id: string;
  tabs: string[];
  activeTab: string;
}
/** `at` is the share of `a`, MIN_AT..MAX_AT. */
export interface Split {
  type: 'split';
  dir: 'row' | 'col';
  a: Node;
  b: Node;
  at: number;
}
export type Node = Group | Split;
export interface Workspace {
  v: 1;
  root: Node;
  active: string;
  tabs: Record<string, Tab>;
}
export type Edge = 'left' | 'right' | 'top' | 'bottom';
export type Preset = 'single' | 'cols' | 'rows' | 'grid';
export type Path = ('a' | 'b')[];

export const CHAT = 'chat';
export const MAX_GROUPS = 4;
export const MIN_AT = 0.15;
export const MAX_AT = 0.85;
export const MIN_PANE = { w: 320, h: 200 };
export const PHONE_WIDTH = breakpoints.phone;

const clampAt = (at: number) => Math.min(MAX_AT, Math.max(MIN_AT, at));

/** Tab ids name what a tab shows, so opening the same thing twice finds the first. Kinds with nothing to name get a counter. */
export function tabId(ws: Workspace | null, kind: TabKind, params: TabParams): string {
  if (kind === 'chat') return CHAT;
  const named = params.handle ?? params.path ?? params.port ?? params.url ?? params.device ?? params.id;
  if (named !== undefined) return `${kind}:${named}`;
  let n = 1;
  while (ws?.tabs[`${kind}:${n}`]) n++;
  return `${kind}:${n}`;
}

const nextGroupId = (ws: Workspace): string => `g${Math.max(0, ...groupsOf(ws.root).map((g) => Number(g.id.slice(1)) || 0)) + 1}`;

/** Chat beside the agent's session terminal, each pane with its own strip; no session, one pane holding Chat. */
export function defaultWorkspace(opts: { terminal?: string } = {}): Workspace {
  const tabs: Record<string, Tab> = { [CHAT]: { id: CHAT, kind: 'chat', params: {} } };
  if (!opts.terminal) return { v: 1, root: group('g1', [CHAT]), active: 'g1', tabs };
  const t = tabId(null, 'terminal', { handle: opts.terminal });
  tabs[t] = { id: t, kind: 'terminal', params: { handle: opts.terminal } };
  return { v: 1, root: { type: 'split', dir: 'row', a: group('g1', [CHAT]), b: group('g2', [t]), at: 0.5 }, active: 'g2', tabs };
}

/** Shows the tab and gives its group the focus. */
export function activate(ws: Workspace, tab: string): Workspace {
  const g = groupOf(ws, tab);
  if (!g) return ws;
  return { ...ws, active: g.id, root: replaceGroup(ws.root, g.id, (x) => ({ ...x, activeTab: tab })) };
}

/** Opens a tab in a group (the focused one by default). The same thing already open is focused, not added again. */
export function openTab(ws: Workspace, kind: TabKind, params: TabParams = {}, into: string = ws.active): Workspace {
  const id = tabId(ws, kind, params);
  if (ws.tabs[id]) return activate(ws, id);
  const target = groupsOf(ws.root).find((g) => g.id === into) ?? groupsOf(ws.root).find((g) => g.id === ws.active)!;
  return {
    ...ws,
    active: target.id,
    tabs: { ...ws.tabs, [id]: { id, kind, params } },
    root: replaceGroup(ws.root, target.id, (g) => ({ ...g, tabs: [...g.tabs, id], activeTab: id })),
  };
}

/** Closing only detaches: it never stops what the tab shows. Chat has no close. */
export function closeTab(ws: Workspace, id: string): Workspace {
  if (id === CHAT || !ws.tabs[id]) return ws;
  const { root, heir } = detach(ws, id);
  const { [id]: _gone, ...tabs } = ws.tabs;
  return { ...ws, tabs, root: root!, active: heir ?? ws.active };
}

/** Drops a tab on a group's strip: into that group at `index` (the end by default). Within a group it reorders. */
export function moveTab(ws: Workspace, id: string, to: string, index?: number): Workspace {
  const from = groupOf(ws, id);
  if (!from || !groupsOf(ws.root).some((g) => g.id === to)) return ws;
  if (from.id === to) {
    const rest = from.tabs.filter((t) => t !== id);
    rest.splice(index ?? rest.length, 0, id);
    return { ...ws, active: to, root: replaceGroup(ws.root, to, (g) => ({ ...g, tabs: rest, activeTab: id })) };
  }
  const { root } = detach(ws, id);
  return {
    ...ws,
    active: to,
    root: replaceGroup(root!, to, (g) => {
      const tabs = [...g.tabs];
      tabs.splice(index ?? tabs.length, 0, id);
      return { ...g, tabs, activeTab: id };
    }),
  };
}

/** Moves a tab to the previous (-1) or next (+1) group in reading order: the keyboard's way to split or merge without a pointer. */
export function moveTabBy(ws: Workspace, id: string, delta: -1 | 1): Workspace {
  const gs = groupsOf(ws.root),
    from = gs.findIndex((g) => g.tabs.includes(id));
  const to = gs[from + delta];
  return from < 0 || !to ? ws : moveTab(ws, id, to.id);
}

/** Groups after taking `id` out and adding one: refused when that passes MAX_GROUPS or splits a lone tab off its own pane. */
function fits(ws: Workspace, id: string, onto?: string): boolean {
  const from = groupOf(ws, id);
  if (!from) return false;
  const alone = from.tabs.length === 1;
  if (alone && (onto === from.id || groupsOf(ws.root).length === 1)) return false;
  return groupsOf(ws.root).length - (alone ? 1 : 0) + 1 <= MAX_GROUPS;
}

const wrap = (existing: Node, fresh: Group, edge: Edge): Split => ({
  type: 'split',
  dir: edge === 'left' || edge === 'right' ? 'row' : 'col',
  a: edge === 'left' || edge === 'top' ? fresh : existing,
  b: edge === 'left' || edge === 'top' ? existing : fresh,
  at: 0.5,
});

/** Drag onto an edge of a pane: splits that pane and puts the tab in the new group. */
export function splitTab(ws: Workspace, id: string, onto: string, edge: Edge): Workspace | null {
  if (!fits(ws, id, onto) || !groupsOf(ws.root).some((g) => g.id === onto)) return null;
  const { root } = detach(ws, id);
  const fresh = group(nextGroupId({ ...ws, root: root! }), [id]);
  return { ...ws, active: fresh.id, root: replaceGroup(root!, onto, (g) => wrap(g, fresh, edge)) };
}

/** Drag to a free edge of the whole view: splits the root. */
export function splitRoot(ws: Workspace, id: string, edge: Edge): Workspace | null {
  if (!fits(ws, id)) return null;
  const { root } = detach(ws, id);
  const fresh = group(nextGroupId({ ...ws, root: root! }), [id]);
  return { ...ws, active: fresh.id, root: wrap(root!, fresh, edge) };
}

/** Rearranges the existing tabs; extra tabs merge into the last group, and there are never more groups than tabs. */
export function applyPreset(ws: Workspace, preset: Preset): Workspace {
  const order = groupsOf(ws.root).flatMap((g) => g.tabs);
  const want = { single: 1, cols: 2, rows: 2, grid: 4 }[preset];
  const n = Math.min(want, order.length);
  const shown = new Set(groupsOf(ws.root).map((g) => g.activeTab));
  const gs = Array.from({ length: n }, (_, i) => {
    const tabs = i < n - 1 ? [order[i]!] : order.slice(i);
    return group(`g${i + 1}`, tabs, tabs.find((t) => shown.has(t)) ?? tabs[0]);
  });
  const two = (dir: 'row' | 'col', a: Node, b: Node): Node => ({ type: 'split', dir, a, b, at: 0.5 });
  let root: Node = gs[0]!;
  if (n === 2) root = two(preset === 'rows' ? 'col' : 'row', gs[0]!, gs[1]!);
  if (n === 3) root = two('col', two('row', gs[0]!, gs[1]!), gs[2]!);
  if (n === 4) root = two('col', two('row', gs[0]!, gs[1]!), two('row', gs[2]!, gs[3]!));
  const focused = groupsOf(ws.root).find((g) => g.id === ws.active)!.activeTab;
  return { ...ws, root, active: gs.find((g) => g.tabs.includes(focused))?.id ?? 'g1' };
}

/** Divider drag: sets the share of the split at `path`, clamped. A double-click is `setSplit(ws, path, 0.5)`. */
export function setSplit(ws: Workspace, path: Path, at: number): Workspace {
  const go = (n: Node, i: number): Node => {
    if (n.type === 'group') return n;
    if (i === path.length) return { ...n, at: clampAt(at) };
    return path[i] === 'a' ? { ...n, a: go(n.a, i + 1) } : { ...n, b: go(n.b, i + 1) };
  };
  return { ...ws, root: go(ws.root, 0) };
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export type Arrangement = { merged: false; rects: { id: string; rect: Rect }[] } | { merged: true; tabs: string[]; activeTab: string };

/**
 * What a window of this size shows. Under the phone width, or when a saved split would leave a pane under MIN_PANE,
 * the groups render as one group in stored order; the saved layout is untouched.
 */
export function arrange(ws: Workspace, width: number, height: number): Arrangement {
  const rects: { id: string; rect: Rect }[] = [];
  const walk = (n: Node, r: Rect) => {
    if (n.type === 'group') {
      rects.push({ id: n.id, rect: r });
      return;
    }
    if (n.dir === 'row') {
      walk(n.a, { ...r, w: r.w * n.at });
      walk(n.b, { ...r, x: r.x + r.w * n.at, w: r.w * (1 - n.at) });
    } else {
      walk(n.a, { ...r, h: r.h * n.at });
      walk(n.b, { ...r, y: r.y + r.h * n.at, h: r.h * (1 - n.at) });
    }
  };
  walk(ws.root, { x: 0, y: 0, w: width, h: height });
  const gs = groupsOf(ws.root);
  if (gs.length > 1 && (width < PHONE_WIDTH || rects.some(({ rect }) => rect.w < MIN_PANE.w || rect.h < MIN_PANE.h))) {
    return { merged: true, tabs: gs.flatMap((g) => g.tabs), activeTab: (gs.find((g) => g.id === ws.active) ?? gs[0]!).activeTab };
  }
  return { merged: false, rects };
}

// ---- saving: in the browser, per agent and daemon ----

export const storageKey = (daemon: string, agent: string): string => `handline.workspace.v1/${daemon}/${agent}`;
export const serialize = (ws: Workspace): string => JSON.stringify(ws);

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x);

function checkNode(x: unknown, seen: Set<string>, ids: Set<string>): Node | null {
  if (!isObj(x)) return null;
  if (x.type === 'group') {
    if (typeof x.id !== 'string' || ids.has(x.id) || !Array.isArray(x.tabs) || !x.tabs.length) return null;
    if (!x.tabs.every((t) => typeof t === 'string' && !seen.has(t) && seen.add(t))) return null;
    if (typeof x.activeTab !== 'string' || !x.tabs.includes(x.activeTab)) return null;
    ids.add(x.id);
    return group(x.id, x.tabs as string[], x.activeTab);
  }
  if (x.type === 'split' && (x.dir === 'row' || x.dir === 'col') && typeof x.at === 'number' && x.at >= MIN_AT && x.at <= MAX_AT) {
    const a = checkNode(x.a, seen, ids),
      b = a && checkNode(x.b, seen, ids);
    return a && b ? { type: 'split', dir: x.dir, a, b, at: x.at } : null;
  }
  return null;
}

/** A workspace from stored JSON, or null when it is not one this code understands (bad shape, a newer `v`, too many groups). Chat is recreated if missing. */
export function parseWorkspace(raw: unknown): Workspace | null {
  if (!isObj(raw) || raw.v !== 1 || !isObj(raw.tabs)) return null;
  const tabs: Record<string, Tab> = {};
  for (const [id, t] of Object.entries(raw.tabs)) {
    if (!isObj(t) || t.id !== id || !TAB_KINDS.includes(t.kind as TabKind) || !isObj(t.params)) return null;
    if ((t.kind === 'chat') !== (id === CHAT)) return null;
    tabs[id] = { id, kind: t.kind as TabKind, params: t.params as TabParams };
  }
  const seen = new Set<string>(),
    ids = new Set<string>();
  const root = checkNode(raw.root, seen, ids);
  if (!root || groupsOf(root).length > MAX_GROUPS) return null;
  if (seen.size !== Object.keys(tabs).length || [...seen].some((t) => !tabs[t])) return null;
  const active = typeof raw.active === 'string' && ids.has(raw.active) ? raw.active : groupsOf(root)[0]!.id;
  const ws: Workspace = { v: 1, root, active, tabs };
  if (tabs[CHAT]) return ws;
  return {
    ...ws,
    tabs: { ...tabs, [CHAT]: { id: CHAT, kind: 'chat', params: {} } },
    root: replaceGroup(root, active, (g) => ({ ...g, tabs: [CHAT, ...g.tabs] })),
  };
}

/** The saved workspace, or `fallback()` when storage is blocked, empty or unreadable. */
export function loadWorkspace(store: KV | null, key: string, fallback: () => Workspace): Workspace {
  try {
    const raw = store?.getItem(key);
    if (raw) return parseWorkspace(JSON.parse(raw)) ?? fallback();
  } catch {
    /* fall through */
  }
  return fallback();
}
export function saveWorkspace(store: KV | null, key: string, ws: Workspace): void {
  try {
    store?.setItem(key, serialize(ws));
  } catch {
    /* storage may be blocked */
  }
}
