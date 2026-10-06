import type { Group, Node, Workspace } from './workspace.ts';

export const groupsOf = (n: Node): Group[] => (n.type === 'group' ? [n] : [...groupsOf(n.a), ...groupsOf(n.b)]);
export const groupOf = (ws: Workspace, tab: string): Group | undefined => groupsOf(ws.root).find((g) => g.tabs.includes(tab));

export const group = (id: string, tabs: string[], activeTab = tabs[0]!): Group => ({ type: 'group', id, tabs, activeTab });

export function mapNode(n: Node, f: (g: Group) => Node | null): Node | null {
  if (n.type === 'group') return f(n);
  const a = mapNode(n.a, f),
    b = mapNode(n.b, f);
  return !a ? b : !b ? a : { ...n, a, b };
}

/** Takes a tab out of its group. A group left empty is removed and its sibling takes the space; `heir` is where focus goes then. */
export function detach(ws: Workspace, id: string): { root: Node | null; heir: string | null } {
  let heir: string | null = null;
  const root = mapNode(ws.root, (g) => {
    if (!g.tabs.includes(id)) return g;
    const tabs = g.tabs.filter((t) => t !== id);
    if (!tabs.length) return null;
    const i = g.tabs.indexOf(id);
    return { ...g, tabs, activeTab: g.activeTab === id ? tabs[Math.min(i, tabs.length - 1)]! : g.activeTab };
  });
  if (root && !groupsOf(root).some((g) => g.id === ws.active)) heir = groupsOf(root)[0]!.id;
  return { root, heir };
}

export function replaceGroup(n: Node, id: string, f: (g: Group) => Node): Node {
  if (n.type === 'group') return n.id === id ? f(n) : n;
  return { ...n, a: replaceGroup(n.a, id, f), b: replaceGroup(n.b, id, f) };
}
