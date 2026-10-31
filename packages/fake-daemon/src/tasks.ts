// The fake daemon's tasks: the rows `tasks.list` and `task.get` answer and the writes that change them, on the tasks topic.
// A task the founder asks for starts as `queued`; withdrawing or cancelling one that has not been delivered discards it.
import { fieldsOf } from '#src/history.ts';
import { TaskFailure } from '#src/taskstore.ts';
import { TaskVerbs } from '#src/taskverbs.ts';

export { TaskFailure };

export class FakeTasks extends TaskVerbs {
  /** The methods this fake answers: the basic ones, and the verbs of the daemon's plan (D3) only when asked for. */
  serves(method: string): boolean {
    return BASIC.has(method) || (this.verbs && VERBS.has(method));
  }
  /** Whether hello lists the method: every method that is not a task one, and the task ones this serves. */
  lists(method: string): boolean {
    return !/^tasks?\./.test(method) || this.serves(method) || /^task\.(request|none)$/.test(method); // those two are the requests module's
  }
  create(p: Record<string, unknown>, person: string, agent = false): number {
    const owner = String(p.owner ?? '');
    if (owner !== 'human' && !this.isAgent(owner)) throw new TaskFailure('bad_request', `no agent ${owner}`);
    if (typeof p.title !== 'string' || !p.title.trim()) throw new TaskFailure('bad_request', 'a task needs a title');
    this.images(String(p.body ?? ''), '', 'body', agent);
    const id = this.nextId++;
    this.seed({
      id,
      from: person,
      to: owner,
      title: p.title,
      body: String(p.body ?? ''),
      state: 'queued',
      time_box: String(p.time_box ?? '2h'),
      clock: { length_s: 7200, default: p.time_box === undefined, start: null, end: null, overdue_since: null, told: 0, left_s: null },
      priority: (p.priority as 1 | 2 | 3 | undefined) ?? undefined,
      estimate: typeof p.estimate === 'number' ? p.estimate : null,
      parent: typeof p.parent === 'number' ? p.parent : null,
      blockers: typeof p.blocked_by === 'number' ? [{ task: this.find(p.blocked_by).id, reason: '', at: this.now() }] : [],
    });
    this.find(id).history![0]!.by = person;
    this.emit(this.view(this.find(id)));
    return id;
  }

  edit(p: Record<string, unknown>, agent = false): void {
    const t = this.find(p.task);
    if (t.state === 'done' || t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is closed`);
    if (p.owner !== undefined && !this.isAgent(String(p.owner))) throw new TaskFailure('bad_request', `no agent ${String(p.owner)}`);
    if (p.body !== undefined) this.images(String(p.body), t.body, 'body', agent);
    const waitOn = typeof p.blocked_by === 'number' ? this.find(p.blocked_by).id : null;
    this.change(t, {
      ...(waitOn !== null && !t.blockers.some((b) => b.task === waitOn)
        ? { blockers: [...t.blockers, { task: waitOn, reason: '', at: this.now() }] }
        : {}),
      ...(p.owner !== undefined ? { to: String(p.owner) } : {}),
      ...fieldsOf(p),
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
}

// The task methods the fake serves; the rest (handoff) are refused as `unsupported`.
const BASIC = new Set([
  'tasks.list',
  'task.get',
  'task.comment.list',
  'task.comment.recipients',
  'task.history',
  'task.time',
  'task.new',
  'task.edit',
  'task.withdraw',
  'task.cancel',
  'task.ask',
]);
const VERBS = new Set([
  'task.done',
  'task.reopen',
  'task.queue',
  'task.draft',
  'task.discard',
  'task.release',
  'task.merge',
  'task.comment.add',
  'task.comment.edit',
  'task.comment.delete',
  'task.comment.send_now',
]);
