// The fake daemon's tasks: the rows `tasks.list` and `task.get` answer and the writes that change them, on the tasks topic.
// A task the founder asks for starts as `queued`; withdrawing or cancelling one that has not been delivered discards it.
import type { HistoryRow, Memo, MemoFile, Task, TaskClock, TaskDetail } from '@handline/contract';
import { seedTasks } from './fixtures.ts';
import { invalidParams, refused, storeFailure } from './errors.ts';
import { changes } from './history.ts';
import { refs, type FakeAttachments } from './attachments.ts';

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
  uploads: FakeAttachments;
}
type Row = Task & {
  body: string;
  memos?: Memo[];
  notes?: { at: string; who: string; text: string }[]; // the notes it was sent back with
  blockers: { task: number; reason: string; at?: string }[];
  clock?: TaskClock | null;
  /** What changed, oldest first; task.history answers it newest first. */
  history?: HistoryRow[];
};

export class FakeTasks {
  private held: Row[] | null = null;
  private nextId = 2000;
  private host: Host;
  private now: () => string;
  /** Serve the verbs of the daemon's plan (D3: done, reopen, queue, draft, discard, merge) too; off, as the real daemon is today. */
  verbs = false;
  /** Who the write now being answered is from, so the history can say who changed what. */
  by = 'founder';

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
  /** Checks and claims the images a write's text names; an agent names only an image some task's text already shows. */
  private images(text: string, was: string, field: string, agent: boolean): void {
    const shown = () => new Set(this.rows.flatMap((r) => [r.body, ...(r.memos ?? []).map((m) => m.text), ...(r.notes ?? []).map((n) => n.text)].flatMap(refs)));
    this.host.uploads.check(text, was, field, agent, shown);
  }
  private emit(t: Task): void {
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
  private open(id: number): boolean {
    const t = this.rows.find((r) => r.id === id);
    return !!t && t.state !== 'done' && t.state !== 'discarded';
  }
  /** As the daemon: behind the open blockers, else on the founder when it is the founder's own, else nothing (the fake has no owners to wait on). */
  private waiting(t: Row): Task['waiting'] {
    if (t.state !== 'queued' && t.state !== 'doing') return null;
    const on = t.blockers.filter((b) => this.open(b.task));
    if (on.length) {
      const tasks = on.map((b) => b.task).sort((a, b) => a - b);
      return { on: 'task', text: `blocked by ${tasks.map((id) => `T${id}`).join(', ')}`, tasks, since: on[0]!.at ?? t.created };
    }
    return t.to === 'human' ? { on: 'founder', text: 'waiting on you', tasks: [], since: t.created } : null;
  }
  private view(row: Row): Task {
    const { body: _body, memos: _memos, notes: _notes, blockers, clock, ...t } = row;
    const ended = t.state === 'done' || t.state === 'discarded';
    return {
      ...t,
      blocked_by: blockers.filter((b) => this.open(b.task)).map((b) => b.task),
      waits: blockers.map((b) => ({ task: b.task, open: this.open(b.task) })),
      waiting: this.waiting(row),
      time: ended ? null : (clock ?? null),
    };
  }
  private change(t: Row, patch: Partial<Row>): void {
    (t.history ??= []).push(...changes(t, patch, this.now(), this.by));
    Object.assign(t, patch);
    this.emit(this.view(t));
    // As the daemon: a discarded task blocks nothing, so it leaves every waits (a done one stays, open false).
    if (patch.state === 'discarded')
      for (const r of this.rows)
        if (r.blockers.some((b) => b.task === t.id)) this.change(r, { blockers: r.blockers.filter((b) => b.task !== t.id) });
  }

  list(p: Record<string, unknown>): { items: Task[]; more: boolean } {
    const limit = Math.min(Number(p.limit ?? 50), 200);
    let rows = this.rows.filter(
      (r) =>
        (p.all === true || p.under !== undefined || (r.state !== 'done' && r.state !== 'discarded')) &&
        (p.my_turn === true ? r.to === 'human' && ['queued', 'doing'].includes(r.state) : p.owner === undefined || r.to === p.owner) &&
        (p.from === undefined || r.from === p.from) &&
        (p.state === undefined || r.state === p.state) &&
        (p.parent === undefined || r.parent === p.parent) &&
        (p.under === undefined || this.below(Number(p.under), r)) &&
        (p.before === undefined || r.id < Number(p.before)),
    );
    rows = rows.sort((a, b) => b.id - a.id);
    return { items: rows.slice(0, limit).map((r) => this.view(r)), more: rows.length > limit };
  }

  /** Whether r is the task top or below it at any depth. */
  private below(top: number, r: Row): boolean {
    for (let at: Row | undefined = r; at; at = at.parent === null ? undefined : this.rows.find((x) => x.id === at!.parent))
      if (at.id === top) return true;
    return false;
  }

  memo(p: Record<string, unknown>, op: 'add' | 'edit' | 'delete', person: string, agent = false): number {
    const t = this.find(p.task);
    const memos = (t.memos ??= []);
    if (op === 'add') {
      this.images(String(p.text), '', 'text', agent);
      const id = memos.reduce((n, m) => Math.max(n, m.id), 0) + 1;
      const files = Array.isArray(p.files) ? p.files.map((f) => this.host.uploads.memoFile(String(f))) : [];
      memos.push({ id, task: t.id, author: person, at: this.now(), text: String(p.text), ...(files.length ? { files } : {}) });
      (t.history ??= []).push({ at: this.now(), by: person, field: 'memo', was: '', now: String(p.text), memo: id });
      return id;
    }
    const i = memos.findIndex((m) => m.id === p.memo);
    if (i < 0) throw new TaskFailure('refused', `memo ${String(p.memo)} does not exist`);
    if (op === 'edit') {
      this.images(String(p.text), memos[i]!.text, 'text', agent);
      (t.history ??= []).push({ at: this.now(), by: person, field: 'memo', was: memos[i]!.text, now: String(p.text), memo: memos[i]!.id });
      memos[i] = { ...memos[i]!, text: String(p.text) };
    } else {
      (t.history ??= []).push({ at: this.now(), by: person, field: 'memo', was: memos[i]!.text, now: '', memo: memos[i]!.id });
      memos.splice(i, 1);
    }
    return memos[i]?.id ?? 0;
  }

  /** A memo that carries files, as `hl task memo add --file` leaves it. */
  memoWithFiles(task: number, author: string, text: string, files: MemoFile[]): number {
    const id = this.memo({ task, text }, 'add', author);
    const m = this.find(task).memos!.find((x) => x.id === id)!;
    m.files = files;
    return id;
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
      discarded: null,
      merged_into: null,
      time: t.state === 'done' || t.state === 'discarded' ? null : (t.clock ?? null),
      parent_title: this.rows.find((r) => r.id === t.parent)?.title ?? null,
      subtasks: this.rows.filter((r) => r.parent === t.id).map((r) => ({ id: r.id, title: r.title, owner: r.to, state: r.state })),
      timeline: [
        ...(t.memos ?? []).map((m) => withParts({ at: m.at, who: m.author, kind: 'memo' as const, text: m.text, memo: m.id, edited_at: m.edited_at ?? null }, m.text)),
        ...(t.notes ?? []).map((n) => withParts({ at: n.at, who: n.who, kind: 'reject' as const, text: n.text, memo: null, edited_at: null }, n.text)),
      ].sort((a, b) => a.at.localeCompare(b.at)),
      people: { recent: [], team: [], others: [] },
    };
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
      ...(waitOn !== null && !t.blockers.some((b) => b.task === waitOn) ? { blockers: [...t.blockers, { task: waitOn, reason: '', at: this.now() }] } : {}),
      ...(p.owner !== undefined ? { to: String(p.owner), ...(t.state === 'answered' ? { state: 'queued', report: null } : {}) } : {}),
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

  /** A done by the holder (the founder too) of a task someone else asked is an answer: it stays theirs until accept, reject or a reassign. */
  done(p: Record<string, unknown>, person: string): string {
    const t = this.find(p.task);
    if (t.state !== 'doing' && t.state !== 'queued') throw new TaskFailure('refused', `task ${t.id} is ${t.state}, not open`);
    const evidence = String(p.evidence ?? '') || null;
    // a person who is no agent is the founder, who holds what is assigned to human
    if (t.from !== person && (person === t.to || (t.to === 'human' && !this.isAgent(person) && this.isAgent(t.from)))) {
      this.change(t, { state: 'answered', report: { by: t.to, evidence: evidence ?? '', at: this.now() } });
      return `task T${t.id} answered (${evidence ?? ''}); it stays with ${t.to} until ${t.from} accepts it or takes it back`;
    }
    this.change(t, { state: 'done', done: this.now(), evidence });
    return `task T${t.id} done`;
  }
  /** The report a person may accept or reject: only the task's opener or the founder. */
  private reportFor(p: Record<string, unknown>, person: string, verb: string): Row {
    const t = this.find(p.task);
    if (t.state !== 'answered' || !t.report) throw new TaskFailure('refused', `task ${t.id} has no answer waiting: nothing to ${verb}`);
    if (person !== t.from && person !== 'founder') throw new TaskFailure('refused', `task ${t.id}'s answer can only be ${verb}ed by whoever it answers to`);
    return t;
  }
  accept(p: Record<string, unknown>, person: string): void {
    const t = this.reportFor(p, person, 'accept');
    this.change(t, { state: 'done', done: this.now(), evidence: t.report!.evidence, report: null });
  }
  /** The report is dropped and the task is queued again for its owner. The note is one line of words, then only its image references. */
  reject(p: Record<string, unknown>, person: string, agent = false): void {
    const note = String(p.note ?? '');
    const first = note.search(/!\[[^\]]*\]\(attachment:/);
    const words = (first < 0 ? note : note.slice(0, first)).replace(/\s+/g, ' ').trim();
    if (!words) throw invalidParams('note is the one line that says what is missing', 'note');
    const images = first < 0 ? '' : note.slice(first);
    if (images.replace(/!\[[^\]]*\]\(attachment:[^)]*\)/g, '').trim())
      throw invalidParams('note is one line of words, then only its image references', 'note');
    const t = this.reportFor(p, person, 'reject');
    this.images(images, '', 'note', agent);
    const kept = [words, ...(images.match(/!\[[^\]]*\]\(attachment:[^)]*\)/g) ?? [])].join('\n');
    this.change(t, { state: 'queued', evidence: null, report: null, notes: [...(t.notes ?? []), { at: this.now(), who: person, text: kept }] });
  }
  reopen(p: Record<string, unknown>, person: string): void {
    const t = this.find(p.task);
    if (t.state !== 'done' || t.from !== person) throw new TaskFailure('refused', 'only a done task that you asked for can be reopened');
    this.change(t, { state: 'queued', done: null });
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
    if (!String(p.reason ?? '').trim()) throw new TaskFailure('bad_request', 'a discard needs a reason');
    if (t.state === 'done' || t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is ${t.state}`);
    this.change(t, { state: 'discarded', done: this.now(), hold_reason: String(p.reason) });
  }
  /** Take a doing task back to queued; the daemon takes no target but needs a reason. */
  release(p: Record<string, unknown>): void {
    if ('to' in p) throw new TaskFailure('bad_request', 'task.release takes no to');
    const t = this.find(p.task);
    if (!String(p.reason ?? '').trim()) throw new TaskFailure('bad_request', 'a release needs a reason');
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

// The task methods the fake serves; the rest (handoff) are refused as `unsupported`.
const BASIC = new Set(['tasks.list', 'task.get', 'task.history', 'task.new', 'task.edit', 'task.withdraw', 'task.cancel', 'task.ask']);
const VERBS = new Set([
  'task.done',
  'task.accept',
  'task.reject',
  'task.reopen',
  'task.queue',
  'task.draft',
  'task.discard',
  'task.release',
  'task.merge',
  'task.memo.add',
  'task.memo.edit',
  'task.memo.delete',
]);

/** Answers one task method, turning the store's refusals into the daemon's errors. */
export function taskCall(
  tasks: FakeTasks,
  method: string,
  p: Record<string, unknown>,
  person: string,
  agent: boolean,
  script: { accepted(said: string, extra?: Record<string, unknown>): unknown },
  ask: (task: number, to: string, text: string) => number = () => {
    throw refused('unsupported', 'the fake daemon does not serve task.ask');
  },
): unknown {
  const ok = (said: string, extra?: Record<string, unknown>) => script.accepted(said, extra);
  tasks.by = person;
  try {
    switch (method) {
      case 'task.history':
        return tasks.history(p);
      case 'tasks.list':
        return tasks.list(p);
      case 'task.get':
        return tasks.get(p, agent);
      case 'task.new':
        return ok('Task asked.', { task: tasks.create(p, person, agent) });
      case 'task.edit':
        return (tasks.edit(p, agent), ok('Task changed.'));
      case 'task.withdraw':
        return (tasks.close(p, 'withdraw'), ok('Task withdrawn.'));
      case 'task.cancel':
        return (tasks.close(p, 'cancel'), ok('Task cancelled.'));
      case 'task.ask': {
        if (!Number.isInteger(p.task)) throw invalidParams('task is a task number', 'task');
        if (typeof p.to !== 'string' || !p.to) throw invalidParams("to is an agent's name", 'to');
        if (typeof p.text !== 'string' || !p.text.trim()) throw invalidParams('text is the question, not empty', 'text');
        tasks.find(p.task);
        return ok('Asked.', { request: ask(p.task as number, p.to, p.text.trim()) });
      }
      case 'task.done':
        return ok(tasks.done(p, person));
      case 'task.accept':
        return (tasks.accept(p, person), ok('Task accepted.'));
      case 'task.reject':
        return (tasks.reject(p, person, agent), ok('Task sent back.'));
      case 'task.reopen':
        return (tasks.reopen(p, person), ok('Task reopened.'));
      case 'task.queue':
        return (tasks.move(p, ['draft'], 'queued'), ok('Queued.'));
      case 'task.draft':
        return (tasks.move(p, ['queued', 'doing'], 'draft'), ok('Back to draft.'));
      case 'task.discard':
        return (tasks.discard(p), ok('Task discarded.'));
      case 'task.release':
        return (tasks.release(p), ok('Task released.'));
      case 'task.merge':
        return (tasks.merge(p), ok('Tasks folded.'));
      case 'task.memo.add':
        return ok('Memo added.', { memo: tasks.memo(p, 'add', person, agent) });
      case 'task.memo.edit':
        return (tasks.memo(p, 'edit', person, agent), ok('Memo changed.'));
      case 'task.memo.delete':
        return (tasks.memo(p, 'delete', person), ok('Memo deleted.'));
      default:
        throw refused('unsupported', `the fake daemon does not serve ${method}`);
    }
  } catch (e) {
    if (!(e instanceof TaskFailure)) throw e;
    throw storeFailure(e);
  }
}
