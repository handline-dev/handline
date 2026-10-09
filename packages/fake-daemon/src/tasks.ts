// The fake daemon's tasks: the rows `tasks.list` and `task.get` answer and the writes that change them, on the tasks topic.
// A task the founder asks for starts as `queued`; withdrawing or cancelling one that has not been delivered discards it.
import type { Task, TaskClock, TaskDetail } from '@handline/contract';
import { seedTasks } from './fixtures.ts';
import { refused, storeFailure } from './errors.ts';

export class TaskFailure extends Error {
  code: 'not_found' | 'refused' | 'bad_request';
  constructor(code: 'not_found' | 'refused' | 'bad_request', message: string) {
    super(message);
    this.code = code;
  }
}

/** What the tasks need of the daemon that hosts them: to emit on a topic, and to know the agents. */
interface Host {
  emit(topic: 'tasks', type: string, f: { data: Record<string, unknown> }): void;
  sessions: Map<string, unknown>;
}
type Row = Task & { body: string; blockers: { task: number; reason: string }[]; clock?: TaskClock | null };

export class FakeTasks {
  private held: Row[] | null = null;
  private nextId = 2000;
  private host: Host;
  private now: () => string;
  /** Serve the verbs of the daemon's plan (D3: done, reopen, queue, draft, discard, merge) too; off, as the real daemon is today. */
  verbs = false;

  constructor(host: Host, now: () => string) {
    this.host = host;
    this.now = now;
  }

  /** The rows; the fixtures are made on first use, when the daemon's clock is there. */
  private get rows(): Row[] {
    if (!this.held) {
      this.held = [];
      seedTasks(this, this.now());
    }
    return this.held;
  }

  /** The methods this fake answers: the basic ones, and the verbs of the daemon's plan (D3) only when asked for. */
  serves(method: string): boolean {
    return BASIC.has(method) || (this.verbs && VERBS.has(method));
  }
  /** Whether hello lists the method: every method that is not a task one, and the task ones this serves. */
  lists(method: string): boolean {
    return !/^tasks?\./.test(method) || this.serves(method);
  }
  private isAgent(name: string): boolean {
    return this.host.sessions.has(name);
  }
  private emit(t: Task): void {
    this.host.emit('tasks', 'task.changed', { data: t as unknown as Record<string, unknown> });
  }

  seed(t: Partial<Row> & Pick<Row, 'id' | 'from' | 'to' | 'title' | 'state'>): void {
    this.rows.push({
      relation: 'manager',
      response: 'async',
      time_box: '2h',
      parent: null,
      created: this.now(),
      delivered: null,
      done: null,
      hold_reason: null,
      block_reason: null,
      evidence: null,
      body: '',
      blockers: [],
      ...t,
    });
  }

  private find(id: unknown): Row {
    const t = this.rows.find((r) => r.id === id);
    if (!t) throw new TaskFailure('not_found', `no task ${String(id)}`);
    return t;
  }
  private view({ body: _body, blockers, clock, ...t }: Row): Task {
    const ended = t.state === 'done' || t.state === 'discarded';
    return { ...t, blocked_by: blockers.map((b) => b.task), time: ended ? null : (clock ?? null) };
  }
  private change(t: Row, patch: Partial<Row>): void {
    Object.assign(t, patch);
    this.emit(this.view(t));
  }

  list(p: Record<string, unknown>): { items: Task[]; more: boolean } {
    const limit = Math.min(Number(p.limit ?? 50), 200);
    let rows = this.rows.filter(
      (r) =>
        (p.all === true || (r.state !== 'done' && r.state !== 'discarded')) &&
        (p.owner === undefined || r.to === p.owner) &&
        (p.from === undefined || r.from === p.from) &&
        (p.state === undefined || r.state === p.state) &&
        (p.parent === undefined || r.parent === p.parent) &&
        (p.before === undefined || r.id < Number(p.before)),
    );
    rows = rows.sort((a, b) => b.id - a.id);
    return { items: rows.slice(0, limit).map((r) => this.view(r)), more: rows.length > limit };
  }

  get(p: Record<string, unknown>): TaskDetail {
    const t = this.find(p.task);
    return {
      ...this.view(t),
      body: t.body,
      deliveries: t.delivered ? [{ at: t.delivered, how: 'typed', outcome: 'delivered' }] : [],
      events: [{ topic: 'tasks', type: 'task.opened', at: t.created, actor: null, agent: t.to, title: t.title, target: null, data: {} }],
      memos: [],
      blockers: t.blockers.map((b) => {
        const held = this.rows.find((r) => r.id === b.task);
        return { ...b, owner: held?.to ?? '', title: held?.title ?? '' };
      }),
      children: this.rows.filter((r) => r.parent === t.id).map((r) => r.id),
      handoffs: [],
      asker: t.from,
      discarded: null,
      merged_into: null,
      time: t.state === 'done' || t.state === 'discarded' ? null : (t.clock ?? null),
      parent_title: this.rows.find((r) => r.id === t.parent)?.title ?? null,
      subtasks: this.rows.filter((r) => r.parent === t.id).map((r) => ({ id: r.id, title: r.title, owner: r.to, state: r.state })),
      timeline: [],
      people: { recent: [], team: [], others: [] },
    };
  }

  create(p: Record<string, unknown>, person: string): number {
    const owner = String(p.owner ?? '');
    if (!this.isAgent(owner)) throw new TaskFailure('bad_request', `no agent ${owner}`);
    if (typeof p.title !== 'string' || !p.title.trim()) throw new TaskFailure('bad_request', 'a task needs a title');
    const id = this.nextId++;
    this.seed({
      id,
      from: person,
      to: owner,
      title: p.title,
      body: String(p.body ?? ''),
      state: 'queued',
      time_box: String(p.time_box ?? '2h'),
      priority: (p.priority as 1 | 2 | 3 | undefined) ?? undefined,
      parent: typeof p.parent === 'number' ? p.parent : null,
    });
    this.emit(this.view(this.find(id)));
    return id;
  }

  edit(p: Record<string, unknown>): void {
    const t = this.find(p.task);
    if (t.state === 'done' || t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is closed`);
    if (p.owner !== undefined && !this.isAgent(String(p.owner))) throw new TaskFailure('bad_request', `no agent ${String(p.owner)}`);
    this.change(t, {
      ...(p.owner !== undefined ? { to: String(p.owner) } : {}),
      ...(p.title !== undefined ? { title: String(p.title) } : {}),
      ...(p.body !== undefined ? { body: String(p.body) } : {}),
      ...(p.time_box !== undefined ? { time_box: String(p.time_box) } : {}),
      ...(p.priority !== undefined ? { priority: p.priority as 1 | 2 | 3 } : {}),
    });
  }

  /** Withdraw takes back a task nobody has started; cancel stops one that is running. */
  close(p: Record<string, unknown>, verb: 'withdraw' | 'cancel'): void {
    const t = this.find(p.task);
    if (verb === 'withdraw' && t.state !== 'queued' && t.state !== 'draft' && t.state !== 'pending')
      throw new TaskFailure('refused', `task ${t.id} has started; cancel it instead`);
    if (verb === 'cancel' && t.state === 'done') throw new TaskFailure('refused', `task ${t.id} is done`);
    if (t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is already discarded`);
    this.change(t, { state: 'discarded', done: this.now() });
  }

  // ---- the verbs of the daemon's task plan (D3), served only when the fake is asked to ----

  done(p: Record<string, unknown>): void {
    const t = this.find(p.task);
    if (t.state !== 'doing' && t.state !== 'queued') throw new TaskFailure('refused', `task ${t.id} is ${t.state}, not open`);
    this.change(t, { state: 'done', done: this.now(), evidence: String(p.evidence ?? '') || null });
  }
  reopen(p: Record<string, unknown>, person: string): void {
    const t = this.find(p.task);
    if (t.state !== 'done' || t.from !== person) throw new TaskFailure('refused', 'only a done task that you asked for can be reopened');
    this.change(t, { state: 'queued', done: null });
  }
  /** Queue or draft a list: the whole call is refused if any of them is in the wrong state. */
  move(p: Record<string, unknown>, from: 'draft' | 'queued', to: 'draft' | 'queued'): void {
    const rows = ((p.tasks as number[]) ?? []).map((id) => this.find(id));
    const bad = rows.find((t) => t.state !== from);
    if (bad) throw new TaskFailure('refused', `task ${bad.id} is ${bad.state}, not ${from}`);
    for (const t of rows) this.change(t, { state: to });
  }
  discard(p: Record<string, unknown>): void {
    const t = this.find(p.task);
    if (!String(p.reason ?? '').trim()) throw new TaskFailure('bad_request', 'a discard needs a reason');
    if (t.state === 'done' || t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is ${t.state}`);
    this.change(t, { state: 'discarded', done: this.now(), hold_reason: String(p.reason) });
  }
  merge(p: Record<string, unknown>): void {
    const into = this.find(p.into);
    const rows = ((p.tasks as number[]) ?? []).map((id) => this.find(id));
    if (rows.some((t) => t.id === into.id)) throw new TaskFailure('refused', 'a task cannot be folded into itself');
    for (const t of rows) this.change(t, { state: 'discarded', done: this.now(), hold_reason: `folded into ${into.id}` });
  }
}

// The task methods the fake serves; the rest (memos, release, handoff) are refused as `unsupported`.
const BASIC = new Set(['tasks.list', 'task.get', 'task.new', 'task.edit', 'task.withdraw', 'task.cancel']);
const VERBS = new Set(['task.done', 'task.reopen', 'task.queue', 'task.draft', 'task.discard', 'task.merge']);

/** Answers one task method, turning the store's refusals into the daemon's errors. */
export function taskCall(
  tasks: FakeTasks,
  method: string,
  p: Record<string, unknown>,
  person: string,
  script: { accepted(said: string, extra?: Record<string, unknown>): unknown },
): unknown {
  const ok = (said: string, extra?: Record<string, unknown>) => script.accepted(said, extra);
  try {
    switch (method) {
      case 'tasks.list':
        return tasks.list(p);
      case 'task.get':
        return tasks.get(p);
      case 'task.new':
        return ok('Task asked.', { task: tasks.create(p, person) });
      case 'task.edit':
        return (tasks.edit(p), ok('Task changed.'));
      case 'task.withdraw':
        return (tasks.close(p, 'withdraw'), ok('Task withdrawn.'));
      case 'task.cancel':
        return (tasks.close(p, 'cancel'), ok('Task cancelled.'));
      case 'task.done':
        return (tasks.done(p), ok('Task done.'));
      case 'task.reopen':
        return (tasks.reopen(p, person), ok('Task reopened.'));
      case 'task.queue':
        return (tasks.move(p, 'draft', 'queued'), ok('Queued.'));
      case 'task.draft':
        return (tasks.move(p, 'queued', 'draft'), ok('Back to draft.'));
      case 'task.discard':
        return (tasks.discard(p), ok('Task discarded.'));
      case 'task.merge':
        return (tasks.merge(p), ok('Tasks folded.'));
      default:
        throw refused('unsupported', `the fake daemon does not serve ${method}`);
    }
  } catch (e) {
    if (!(e instanceof TaskFailure)) throw e;
    throw storeFailure(e);
  }
}
