// Who a task's detail offers: the people to assign it to (who acted on it, who is near in the team tree, everyone else)
// and the people worth a reply (everyone the task's history names). Decided here so the screens only draw it.

export interface PeopleTask {
  from: string;
  to: string;
  events: readonly { at: string; agent?: string | null; actor?: { kind: string; name: string } | null }[];
  memos?: readonly { at: string; author: string }[];
}
export interface PeopleTeam {
  agents: readonly { name: string; parent: string | null }[];
}
export interface AssignGroup {
  key: 'acted' | 'near' | 'rest';
  label: string;
  names: string[];
}

/** Names that are the system itself, not someone to talk to. */
const SYSTEM = new Set(['task-board', 'schedule', 'system']);

/** Everyone the task's history names, the most recent first, then its opener and holder; each once. */
export function involved(t: PeopleTask): string[] {
  const seen: { name: string; at: string }[] = [];
  for (const e of t.events) for (const name of [e.agent, e.actor && e.actor.kind !== 'daemon' ? e.actor.name : null]) if (name) seen.push({ name, at: e.at });
  for (const m of t.memos ?? []) seen.push({ name: m.author, at: m.at });
  const byTime = seen.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0)).map((s) => s.name);
  return [...new Set([...byTime, t.to, t.from])].filter((n) => n && !SYSTEM.has(n));
}

/** Steps between two agents along the reports-to tree; Infinity when they are in different trees. */
export function treeDistance(team: PeopleTeam, a: string, b: string): number {
  const parent = new Map(team.agents.map((x) => [x.name, x.parent]));
  const up = (n: string) => {
    const path = [n];
    for (let p = parent.get(n); p && !path.includes(p); p = parent.get(p)) path.push(p);
    return path;
  };
  const pa = up(a);
  const pb = up(b);
  const meet = pa.findIndex((n) => pb.includes(n));
  return meet < 0 ? Infinity : meet + pb.indexOf(pa[meet]!);
}

/** The assign picker: who acted on the task (latest first), then the agents nearest the holder in the tree, then the rest by name. */
export function assignGroups(t: PeopleTask, team: PeopleTeam, known: readonly string[], me: string): AssignGroup[] {
  const all = [...new Set([...known, ...team.agents.map((a) => a.name)])].filter((n) => n !== me && !SYSTEM.has(n));
  const acted = involved(t).filter((n) => all.includes(n) && n !== t.to);
  const left = all.filter((n) => !acted.includes(n) && n !== t.to);
  const dist = new Map(left.map((n) => [n, Math.min(treeDistance(team, t.to, n), treeDistance(team, t.from, n))]));
  const near = left.filter((n) => dist.get(n)! <= 2).sort((a, b) => dist.get(a)! - dist.get(b)! || a.localeCompare(b));
  const rest = left.filter((n) => !near.includes(n)).sort((a, b) => a.localeCompare(b));
  return [
    { key: 'acted' as const, label: 'Worked on this task', names: acted },
    { key: 'near' as const, label: 'Close in the team', names: near },
    { key: 'rest' as const, label: 'Everyone else', names: rest },
  ].filter((g) => g.names.length);
}

/** The reply chips: the people the task names, other than the person looking. */
export const replyTargets = (t: PeopleTask, me: string): string[] => involved(t).filter((n) => n !== me && n !== 'human');
