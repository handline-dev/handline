// The fake daemon's stats.retro: the tasks, doing windows and token calls the retro fixtures name (tasks 9001 to 9003).
import type { Retro } from '@handline/contract';
import { badRequest, Failure } from '#src/errors.ts';

interface Window {
  task: number;
  agent: string;
  from: number;
  to: number;
}
interface Call {
  agent: string;
  ts: number;
  tokens: number;
}

/** 9001 is a founder's task with 9002 under it, both done; 9003 answers to an agent. */
const FOUNDER_TASKS = new Map([[9001, { created: 1000, ended: 4600, subtree: [9001, 9002] }]]);
const AGENT_TASKS = new Set([9003]);
const WINDOWS: Window[] = [
  { task: 9002, agent: 'grp.one', from: 1000, to: 2800 },
  { task: 9001, agent: 'grp.two', from: 2800, to: 4600 },
];
/** grp.one's call at 3000 is after its window, so it counts to no one. */
const CALLS: Call[] = [
  { agent: 'grp.one', ts: 1500, tokens: 100 },
  { agent: 'grp.one', ts: 3000, tokens: 500 },
  { agent: 'grp.two', ts: 3000, tokens: 200 },
];
const USD_PER_TOKEN = 0.01;

export function retroOf(task: number): Retro {
  const t = FOUNDER_TASKS.get(task);
  if (!t)
    throw AGENT_TASKS.has(task) ? badRequest('not a founder’s task', 'task') : new Failure('not_found', 'no such task', { what: 'task' });
  const rows = new Map<string, { seconds: number; tokens: number; tasks: number[] }>();
  for (const w of WINDOWS.filter((w) => t.subtree.includes(w.task))) {
    const r = rows.get(w.agent) ?? { seconds: 0, tokens: 0, tasks: [] };
    r.seconds += w.to - w.from;
    r.tokens += CALLS.filter((c) => c.agent === w.agent && c.ts > w.from && c.ts <= w.to).reduce((n, c) => n + c.tokens, 0);
    r.tasks.push(w.task);
    rows.set(w.agent, r);
  }
  const agents = [...rows]
    .map(([agent, r]) => ({
      agent,
      doing_seconds: r.seconds,
      tokens: r.tokens,
      cost: r.tokens * USD_PER_TOKEN,
      tasks: r.tasks.sort((a, b) => a - b),
    }))
    .sort((a, b) => b.tokens - a.tokens || a.agent.localeCompare(b.agent));
  const tokens = agents.reduce((n, a) => n + a.tokens, 0);
  return { task, wall_seconds: t.ended - t.created, tokens, cost: tokens * USD_PER_TOKEN, agents };
}
