// The fake daemon's task rows: what tasks.list and task.get answer, and the one place a row changes and the tasks topic is told.
import type { FakeInbox } from '#src/inbox.ts';
import type { HistoryRow, Memo, Task, TaskClock, TaskDetail, TimeSegment } from '@handline/contract';
import { seedTasks } from '#src/seedtasks.ts';
import { changes } from '#src/history.ts';
import { refs, type FakeAttachments } from '#src/attachments.ts';

export class TaskFailure extends Error {
  code: 'not_found' | 'refused' | 'bad_request';
  constructor(code: 'not_found' | 'refused' | 'bad_request', message: string) {
    super(message);
    this.code = code;
  }
}

/** What the tasks need of the daemon that hosts them: to emit on a topic, and to know the agents. */
export interface Host {
  emit(topic: 'tasks', type: string, f: { data: Record<string, unknown> }): void;
  sessions: Map<string, unknown>;
  uploads: FakeAttachments;
  inbox: FakeInbox;
}
export type Row = Task & {
  body: string;
  memos?: Memo[];
  notes?: { at: string; who: string; text: string }[]; // the notes it was sent back with
  blockers: { task: number; reason: string; at?: string }[];
  clock?: TaskClock | null;
  /** What task.time answers for it when it is not the plain queued-then-working of its delivery. */
  segments?: TimeSegment[];
  /** Story points; task.time serves it. */
  estimate?: number | null;
  /** What changed, oldest first; task.history answers it newest first. */
  history?: HistoryRow[];
};

/** `@human` in a comment: at the start or after a character that is not part of a name. */
export class TaskStore {
  protected held: Row[] | null = null;
  protected nextId = 2000;
  protected host: Host;
  readonly now: () => string;
  /** Serve the verbs of the daemon's plan (D3: done, reopen, queue, draft, discard, merge) too; off, as the real daemon is today. */
  verbs = false;
  /** Who the write now being answered is from, so the history can say who changed what. */
  by = 'founder';

  constructor(host: Host, now: () => string) {
    this.host = host;
    this.now = now;
  }

  /** The rows; the fixtures are made on first use, when the daemon's clock is there. */
  protected get rows(): Row[] {
    if (!this.held) {
      this.held = [];
      seedTasks(this, this.now());
    }
    return this.held;
  }

  protected isAgent(name: string): boolean {
    return this.host.sessions.has(name);
  }
  /** Checks and claims the images a write's text names; an agent names only an image some task's text already shows. */
  protected images(text: string, was: string, field: string, agent: boolean): void {
    const shown = () =>
      new Set(
        this.rows.flatMap((r) => [r.body, ...(r.memos ?? []).map((m) => m.text), ...(r.notes ?? []).map((n) => n.text)].flatMap(refs)),
      );
    this.host.uploads.check(text, was, field, agent, shown);
  }
  protected emit(t: Task): void {
    this.host.emit('tasks', 'task.changed', { data: t as unknown as Record<string, unknown> });
  }

  seed(t: Partial<Row> & Pick<Row, 'id' | 'from' | 'to' | 'title' | 'state'>): void {
    this.rows.push({
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
    const row = this.rows[this.rows.length - 1]!;
    row.history ??= [
      { at: row.created, by: row.from, field: 'state', was: '', now: 'queued' },
      { at: row.created, by: row.from, field: 'assignee', was: '', now: row.to },
    ];
  }

  find(id: unknown): Row {
    const t = this.rows.find((r) => r.id === id);
    if (!t) throw new TaskFailure('not_found', `no task ${String(id)}`);
    return t;
  }
  protected open(id: number): boolean {
    const t = this.rows.find((r) => r.id === id);
    return !!t && t.state !== 'done' && t.state !== 'discarded';
  }
  /** As the daemon: behind the open blockers, else on the founder when it is the founder's own, else nothing (the fake has no owners to wait on). */
  protected waiting(t: Row): Task['waiting'] {
    if (t.state !== 'queued' && t.state !== 'doing') return null;
    const on = t.blockers.filter((b) => this.open(b.task));
    if (on.length) {
      const tasks = on.map((b) => b.task).sort((a, b) => a - b);
      return { on: 'task', text: `blocked by ${tasks.map((id) => `T${id}`).join(', ')}`, tasks, since: on[0]!.at ?? t.created };
    }
    return t.to === 'human' ? { on: 'founder', text: 'needs you', tasks: [], since: t.created } : null;
  }
  protected view(row: Row): Task {
    const { body: _body, memos: _memos, notes: _notes, blockers, clock, estimate: _estimate, ...t } = row;
    const ended = t.state === 'done' || t.state === 'discarded';
    return {
      ...t,
      blocked_by: blockers.filter((b) => this.open(b.task)).map((b) => b.task),
      waits: blockers.map((b) => ({ task: b.task, open: this.open(b.task) })),
      waiting: this.waiting(row),
      time: ended ? null : (clock ?? null),
    };
  }
  protected change(t: Row, patch: Partial<Row>): void {
    (t.history ??= []).push(...changes(t, patch, this.now(), this.by));
    Object.assign(t, patch);
    this.emit(this.view(t));
    // As the daemon: a discarded task blocks nothing, so it leaves every waits (a done one stays, open false).
    if (patch.state === 'discarded')
      for (const r of this.rows)
        if (r.blockers.some((b) => b.task === t.id)) this.change(r, { blockers: r.blockers.filter((b) => b.task !== t.id) });
  }

  list(p: Record<string, unknown>): { items: Task[]; more: boolean; next?: string } {
    const limit = Math.min(Number(p.limit ?? 50), 200);
    const byClose = p.order === 'closed';
    if (byClose && p.state !== 'done' && p.state !== 'discarded') throw new TaskFailure('bad_request', 'order closed needs state done or discarded');
    if (byClose && p.before !== undefined) throw new TaskFailure('bad_request', 'order closed pages by cursor, not before');
    if (!byClose && p.cursor !== undefined) throw new TaskFailure('bad_request', 'cursor goes with order closed');
    const from = byClose && p.cursor !== undefined ? this.closedFrom(String(p.cursor)) : null;
    let rows = this.rows.filter(
      (r) =>
        (p.all === true || p.under !== undefined || (r.state !== 'done' && r.state !== 'discarded')) &&
        (p.my_turn === true ? r.to === 'human' && ['queued', 'doing'].includes(r.state) : p.owner === undefined || r.to === p.owner) &&
        (p.from === undefined || r.from === p.from) &&
        (p.state === undefined || r.state === p.state) &&
        (p.parent === undefined || r.parent === p.parent) &&
        (p.under === undefined || this.below(Number(p.under), r)) &&
        (p.before === undefined || r.id < Number(p.before)) &&
        (!from || (r.done ?? '') < from.at || ((r.done ?? '') === from.at && r.id < from.id)),
    );
    rows = rows.sort((a, b) => (byClose ? (b.done ?? '').localeCompare(a.done ?? '') : 0) || b.id - a.id);
    const page = rows.slice(0, limit);
    const last = page[page.length - 1];
    const more = rows.length > limit;
    return {
      items: page.map((r) => this.view(r)),
      more,
      ...(byClose && more && last ? { next: btoa(`${last.done ?? ''}|${last.id}`) } : {}),
    };
  }

  /** The close time and id a closed-order cursor stands for. */
  private closedFrom(cursor: string): { at: string; id: number } {
    try {
      const [at, id] = atob(cursor).split('|');
      if (at !== undefined && Number(id) > 0) return { at, id: Number(id) };
    } catch {
      /* falls to the refusal */
    }
    throw new TaskFailure('bad_request', 'cursor is not one this daemon gave');
  }

  /** Whether r is the task top or below it at any depth. */
  protected below(top: number, r: Row): boolean {
    for (let at: Row | undefined = r; at; at = at.parent === null ? undefined : this.rows.find((x) => x.id === at!.parent))
      if (at.id === top) return true;
    return false;
  }

  /** Every change to a task, newest first (rows of one edit keep the order they were made in). */
  history(p: Record<string, unknown>): { task: number; rows: HistoryRow[] } {
    const t = this.find(p.task);
    return { task: t.id, rows: [...(t.history ?? [])].sort((a, b) => b.at.localeCompare(a.at)) };
  }

  get(p: Record<string, unknown>, agent = false): TaskDetail {
    const t = this.find(p.task);
    const parts = (text: string) => this.host.uploads.parts(text, agent);
    const withParts = <T extends object>(o: T, text: string) => {
      const cut = parts(text);
      return cut ? { ...o, parts: cut } : o;
    };
    const memos = (t.memos ?? []).map((m) => withParts(m, m.text));
    const bodyParts = parts(t.body);
    return {
      ...this.view(t),
      body: t.body,
      ...(bodyParts ? { body_parts: bodyParts } : {}),
      deliveries: t.delivered ? [{ at: t.delivered, how: 'typed', outcome: 'delivered' }] : [],
      events: [{ topic: 'tasks', type: 'task.opened', at: t.created, actor: null, agent: t.to, title: t.title, target: null, data: {} }],
      memos,
      blockers: t.blockers.map((b) => {
        const held = this.rows.find((r) => r.id === b.task);
        return { ...b, owner: held?.to ?? '', title: held?.title ?? '' };
      }),
      children: this.rows.filter((r) => r.parent === t.id).map((r) => r.id),
      handoffs: [],
      asker: t.from,
      discarded:
        t.state === 'discarded' ? { by: t.from, reason: t.hold_reason ?? '', at: t.done ?? this.now(), withdrew: [], batch: null } : null,
      merged_into: null,
      time: t.state === 'done' || t.state === 'discarded' ? null : (t.clock ?? null),
      parent_title: this.rows.find((r) => r.id === t.parent)?.title ?? null,
      subtasks: this.rows.filter((r) => r.parent === t.id).map((r) => ({ id: r.id, title: r.title, owner: r.to, state: r.state })),
      timeline: [
        ...(t.memos ?? []).map((m) =>
          withParts({ at: m.at, who: m.author, kind: 'memo' as const, text: m.text, memo: m.id, edited_at: m.edited_at ?? null }, m.text),
        ),
        ...(t.notes ?? []).map((n) =>
          withParts({ at: n.at, who: n.who, kind: 'reject' as const, text: n.text, memo: null, edited_at: null }, n.text),
        ),
      ].sort((a, b) => a.at.localeCompare(b.at)),
      people: { recent: [], team: [], others: [] },
    };
  }
}
