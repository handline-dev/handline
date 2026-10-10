// The fake daemon's workroom: the repos, the worktrees with their status labels, and agent.create, a long-running operation that
// registers an agent, makes its worktree and starts it, reported on the operations topic.
import type { Operation, Timestamp } from '@handline/contract';
import { AGENTS, type AgentFixture } from './fixtures.ts';
import { Failure, badRequest, refused } from './errors.ts';
import { newSession, view, type Session } from './session.ts';

/** What the workroom needs of the daemon that hosts it: its topics and its agents. */
interface Host {
  emit(topic: 'operations' | 'agents', type: string, f: { agent?: string; title?: string; data: Record<string, unknown> }): void;
  sessions: Map<string, Session>;
}
type Accepted = (said: string, extra?: object) => unknown;

/** The repos the daemon knows (a conformance run names `/work/handline` as create_repo), and the branches each has. */
const REPOS = [
  { name: 'handline', path: '/work/handline', default_branch: 'main', branches: ['main'] },
  { name: 'orca', path: '/work/orca', default_branch: 'develop', branches: ['develop', 'main'] },
];
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
const STEPS = ['registering the agent', 'making its worktree', 'starting the agent'];
const METHODS = /^(repos\.list|worktrees\.list|worktree\.set_status|agent\.create|operation\.get)$/;

export class FakeWork {
  /** How long each step of agent.create takes; a test that wants to see a step sets it. */
  stepMs = 0;
  private ops = new Map<string, Operation>();
  private labels = new Map<string, string>();
  private count = 0;

  private host: Host;
  private now: () => Timestamp;

  constructor(host: Host, now: () => Timestamp) {
    this.host = host;
    this.now = now;
  }

  owns = (name: string): boolean => METHODS.test(name);
  lists = (): boolean => true;

  call(method: string, p: Record<string, unknown>, accepted: Accepted): unknown {
    switch (method) {
      case 'repos.list':
        return { repos: REPOS.map(({ branches: _, ...r }) => r) };
      case 'worktrees.list':
        return { worktrees: this.worktrees(p) };
      case 'worktree.set_status':
        return this.setStatus(p, accepted);
      case 'operation.get':
        return this.operation(p.operation);
      default:
        return this.create(p, accepted);
    }
  }

  private worktrees(p: Record<string, unknown>) {
    for (const k of ['repo', 'owner']) if (p[k] !== undefined && typeof p[k] !== 'string') throw badRequest(`${k} is a string`, k);
    const all = [...this.host.sessions.values()].map((s) => s.fx);
    return all
      .filter((fx) => (p.owner === undefined || fx.name === p.owner) && (p.repo === undefined || p.repo === repoOf(fx).name))
      .map((fx) => ({
        path: fx.worktree,
        repo: repoOf(fx).name,
        branch: fx.branch,
        owner: fx.name,
        parent: all.find((x) => x.name === fx.parent)?.worktree ?? null,
        children: all.filter((x) => x.parent === fx.name).map((x) => x.worktree),
        status: this.labels.get(fx.worktree) ?? (fx.name === 'handline.architect' ? 'dirty' : 'clean'),
        changed: fx.name === 'handline.architect' ? [{ path: 'docs/pc-app-gaps.md', status: 'M', added: 12, deleted: 3 }] : [],
      }));
  }

  private setStatus(p: Record<string, unknown>, accepted: Accepted) {
    if (typeof p.path !== 'string' || !p.path) throw badRequest('path is a worktree path', 'path');
    if (typeof p.status !== 'string' || !p.status || p.status.length > 40) throw badRequest('status is a label of 1 to 40 characters', 'status');
    if (![...this.host.sessions.values()].some((s) => s.fx.worktree === p.path)) throw notFound(`${p.path} is not a worktree an agent works in`, 'worktree');
    this.labels.set(p.path, p.status);
    return accepted(`Status is ${p.status}.`);
  }

  private operation(id: unknown): Operation {
    if (typeof id !== 'string') throw badRequest('operation is a string', 'operation');
    const op = this.ops.get(id);
    if (!op) throw notFound(`no operation ${id}`, 'operation');
    return { ...op };
  }

  private create(p: Record<string, unknown>, accepted: Accepted) {
    const { name, role, repo, base, parent } = p;
    if (typeof name !== 'string' || !/^[^./\s][^/\s]*$/.test(name)) throw badRequest('name is plain text: no /, no leading . and no spaces', 'name');
    if (typeof role !== 'string' || !role) throw badRequest('role is the agent’s role, a string', 'role');
    for (const k of ['model', 'base', 'parent', 'repo'] as const) if (p[k] !== undefined && typeof p[k] !== 'string') throw badRequest(`${k} is a string`, k);
    if (p.effort !== undefined && !EFFORTS.includes(String(p.effort))) throw badRequest(`effort is one of ${EFFORTS.join(', ')}`, 'effort');
    if (p.account !== undefined) throw badRequest("a new agent's account is chosen when it starts; none can be named yet", 'account');
    const found = REPOS.find((r) => r.path === repo);
    if (!found) throw notFound(`no repo ${String(repo)}`, 'repo');
    if (parent !== undefined && !this.host.sessions.has(String(parent))) throw notFound(`no agent ${String(parent)}`, 'agent');
    if (parent !== undefined && !name.startsWith(`${String(parent)}.`)) throw badRequest(`under ${String(parent)} the name is ${String(parent)}.<name>`, 'name');
    if (this.host.sessions.has(name)) throw refused('state', `${name} is already an agent`);
    const id = `op-${++this.count}`;
    const op: Operation = { id, kind: 'agent.create', state: 'running', step: null, steps_done: 0, steps_total: STEPS.length, message: null, result: null, agent: name };
    this.ops.set(id, op);
    const fx: AgentFixture = { ...AGENTS[0]!, name, role, state: 'parked', account: null, task: null, parent: typeof parent === 'string' ? parent : null, worktree: `${found.path}/.worktrees/${name}`, branch: `agent/${name}`, session: null };
    setImmediate(() => void this.run(op, fx, typeof base === 'string' ? base : 'main', found.branches, found.name));
    return accepted(`Creating ${name}.`, { operation: id });
  }

  private async run(op: Operation, fx: AgentFixture, base: string, branches: string[], repo: string): Promise<void> {
    const report = (o: Partial<Operation>) => {
      Object.assign(op, o);
      this.host.emit('operations', 'operation.progress', { agent: fx.name, title: `Creating ${fx.name}`, data: { ...op } });
    };
    report({ step: STEPS[0] });
    await this.wait();
    const s = newSession(fx, this.now());
    this.host.sessions.set(fx.name, s);
    this.host.emit('agents', 'agent.changed', { agent: fx.name, data: view(s) as unknown as Record<string, unknown> });
    report({ steps_done: 1, step: STEPS[1] });
    await this.wait();
    if (!branches.includes(base)) return report({ state: 'failed', message: `no branch ${base} in ${repo}`, result: { agent: fx.name, registered: true } });
    report({ steps_done: 2, step: STEPS[2] });
    await this.wait();
    const up = newSession({ ...fx, state: 'idle', session: `s-${fx.name}` }, this.now());
    this.host.sessions.set(fx.name, up);
    this.host.emit('agents', 'agent.changed', { agent: fx.name, data: view(up) as unknown as Record<string, unknown> });
    report({ state: 'done', steps_done: 3, step: null, message: null, result: { agent: fx.name, registered: true } });
  }

  private wait = (): Promise<void> => new Promise((done) => setTimeout(done, this.stepMs));
}

const repoOf = (fx: AgentFixture) => REPOS.find((r) => fx.worktree.startsWith(`${r.path}/`)) ?? REPOS[0]!;
const notFound = (message: string, what: string) => new Failure('not_found', message, { what });
