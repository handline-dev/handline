// The verbs of the daemon's task plan (D3), served only when the fake is asked to.
import { TaskComments } from '#src/taskcomments.ts';
import { TaskFailure } from '#src/taskstore.ts';

export class TaskVerbs extends TaskComments {
  /** A done closes the task; a review is a task.handoff of the task itself. */
  done(p: Record<string, unknown>): string {
    const t = this.find(p.task);
    if (t.state !== 'doing' && t.state !== 'queued') throw new TaskFailure('refused', `task ${t.id} is ${t.state}, not open`);
    const evidence = String(p.evidence ?? '') || 'Done';
    this.change(t, { state: 'done', done: this.now(), evidence });
    this.host.inbox.closeAsks(t.id);
    return `task T${t.id} done`;
  }
  reopen(p: Record<string, unknown>, person: string): void {
    const t = this.find(p.task);
    if ((t.state !== 'done' && t.state !== 'discarded') || t.from !== person)
      throw new TaskFailure('refused', 'only a done or discarded task that you asked for can be reopened');
    this.change(t, { state: t.state === 'done' ? 'queued' : 'draft', done: null });
  }
  /** Queue or draft a list: the whole call is refused if any of them is in the wrong state. */
  move(p: Record<string, unknown>, from: readonly string[], to: 'draft' | 'queued'): void {
    const rows = ((p.tasks as number[]) ?? []).map((id) => this.find(id));
    const bad = rows.find((t) => !from.includes(t.state));
    if (bad) throw new TaskFailure('refused', `task ${bad.id} is ${bad.state}, not ${from.join(' or ')}`);
    for (const t of rows) this.change(t, { state: to });
  }
  discard(p: Record<string, unknown>): void {
    const t = this.find(p.task);
    if (t.state === 'done' || t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is ${t.state}`);
    this.change(t, { state: 'discarded', done: this.now(), hold_reason: String(p.reason ?? '').trim() || 'Discarded' });
    this.host.inbox.closeAsks(t.id);
  }
  /** Take a doing task back to queued; the daemon takes no target and no reason. */
  release(p: Record<string, unknown>): void {
    if ('to' in p) throw new TaskFailure('bad_request', 'task.release takes no to');
    const t = this.find(p.task);
    if (t.state !== 'doing') throw new TaskFailure('refused', `task ${t.id} is ${t.state}, not doing`);
    this.change(t, { state: 'queued' });
  }
  merge(p: Record<string, unknown>): void {
    const into = this.find(p.into);
    const rows = ((p.tasks as number[]) ?? []).map((id) => this.find(id));
    if (rows.some((t) => t.id === into.id)) throw new TaskFailure('refused', 'a task cannot be folded into itself');
    for (const t of rows) this.change(t, { state: 'discarded', done: this.now(), hold_reason: `folded into ${into.id}` });
  }
}
