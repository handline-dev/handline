// The task graph as numbers, no drawing: which tasks are nodes, which wait on which, why each waits, and where each sits. Both apps
// draw the result (web as DOM SVG, phone with react-native-svg). An arrow goes from the task that holds to the task that waits, so
// what is done or free sits at the top and what is blocked sinks to the bottom.
import dagre from '@dagrejs/dagre';
import { WAIT_WORDS, blockedBy } from '@handline/app-ui';
import type { Task } from '@handline/client';
import { CLOSED } from './task-table.ts';
import { isPerson } from './chat.ts';

export const NODE_W = 168;
export const NODE_H = 44;

export interface GraphNode {
  id: number;
  title: string;
  state: string;
  /** The task's owner is the person looking. */
  mine: boolean;
  /** Why it waits, in the shared words; null when it does not. */
  reason: string | null;
  /** Top-left corner. */
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface GraphEdge {
  /** The holder and the waiter. */
  from: number;
  to: number;
  label: string;
  points: { x: number; y: number }[];
}
export interface TaskGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  width: number;
  height: number;
}

/** Request states in which the request still waits for its target. */
const OPEN_REQUEST = ['queued', 'typed', 'landed'];

/** Why one task waits: the tasks that hold it, else a person's open request, else its own block or hold text; null when nothing. */
export function waitReason(t: Task, me: string, open: ReadonlySet<number>): string | null {
  const holders = (t.blocked_by ?? []).filter((n) => open.has(n));
  if (holders.length) return blockedBy(holders);
  if (t.requests?.some((r) => isPerson(r.to, me) && OPEN_REQUEST.includes(r.state))) return WAIT_WORDS.founder;
  return t.block_reason || t.hold_reason || null;
}

/** The open tasks, and with `closed` the done ones as well, laid out top to bottom along "holds". */
export function taskGraph(tasks: readonly Task[], opts: { me: string; closed?: boolean }): TaskGraph {
  const list = tasks.filter((t) => opts.closed || !CLOSED.includes(t.state));
  const ids = new Set(list.map((t) => t.id));
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 56, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const t of list) g.setNode(String(t.id), { width: NODE_W, height: NODE_H });
  const pairs: [number, number][] = [];
  for (const t of list) for (const h of t.blocked_by ?? []) if (ids.has(h) && h !== t.id) pairs.push([h, t.id]);
  for (const [h, w] of pairs) g.setEdge(String(h), String(w));
  dagre.layout(g);
  const nodes = list.map((t): GraphNode => {
    const n = g.node(String(t.id))!;
    return {
      id: t.id,
      title: t.title,
      state: t.state,
      mine: t.to === opts.me,
      reason: waitReason(t, opts.me, ids),
      x: n.x - NODE_W / 2,
      y: n.y - NODE_H / 2,
      w: NODE_W,
      h: NODE_H,
    };
  });
  const edges = pairs.map(([from, to]): GraphEdge => {
    const e = g.edge(String(from), String(to));
    return { from, to, label: blockedBy([from]), points: e.points };
  });
  const { width = 0, height = 0 } = g.graph();
  return { nodes, edges, width, height };
}
