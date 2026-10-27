// The fake daemon's tasks: the rows `tasks.list` and `task.get` answer and the writes that change them, on the tasks topic.
// A task the founder asks for starts as `queued`; withdrawing or cancelling one that has not been delivered discards it.
import type { FakeInbox } from '#src/inbox.ts';
import type { HistoryRow, Memo, MemoFile, MemoReader, Task, TaskClock, TaskDetail, TimeSegment } from '@handline/contract';
import { seedTasks } from '#src/fixtures.ts';
import { changes, fieldsOf } from '#src/history.ts';
import { refs, type FakeAttachments } from '#src/attachments.ts';

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
  inbox: FakeInbox;
}
type Row = Task & {
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
const NAMES_HUMAN = /(?:^|[^A-Za-z0-9._@-])@human(?:[^A-Za-z0-9_-]|$)/;

export class FakeTasks {
  private held: Row[] | null = null;
  private nextId = 2000;
  private host: Host;
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
    return !/^tasks?\./.test(method) || this.serves(method) || /^task\.(request|none)$/.test(method); // those two are the requests module's
  }
  private isAgent(name: string): boolean {
    return this.host.sessions.has(name);
  }
  /** Checks and claims the images a write's text names; an agent names only an image some task's text already shows. */
  private images(text: string, was: string, field: string, agent: boolean): void {
    const shown = () =>
      new Set(
        this.rows.flatMap((r) => [r.body, ...(r.memos ?? []).map((m) => m.text), ...(r.notes ?? []).map((n) => n.text)].flatMap(refs)),
      );
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
    return t.to === 'human' ? { on: 'founder', text: 'needs you', tasks: [], since: t.created } : null;
  }
  private view(row: Row): Task {
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
      if (p.reply !== undefined && !memos.some((m) => m.id === p.reply))
        throw new TaskFailure('refused', `memo ${String(p.reply)} does not exist`);
      memos.push({
        id,
        task: t.id,
        author: person,
        at: this.now(),
        text: String(p.text),
        ...(p.reply === undefined ? {} : { reply_to: Number(p.reply) }),
        ...(files.length ? { files } : {}),
      });
      (t.history ??= []).push({ at: this.now(), by: person, field: 'memo', was: '', now: String(p.text), memo: id });
      this.askFor(t, memos[memos.length - 1]!, true);
      return id;
    }
    const i = memos.findIndex((m) => m.id === p.memo);
    if (i < 0) throw new TaskFailure('refused', `memo ${String(p.memo)} does not exist`);
    if (op === 'edit') {
      this.images(String(p.text), memos[i]!.text, 'text', agent);
      (t.history ??= []).push({ at: this.now(), by: person, field: 'memo', was: memos[i]!.text, now: String(p.text), memo: memos[i]!.id });
      memos[i] = { ...memos[i]!, text: String(p.text) };
      this.askFor(t, memos[i]!, false);
    } else {
      (t.history ??= []).push({ at: this.now(), by: person, field: 'memo', was: memos[i]!.text, now: '', memo: memos[i]!.id });
      this.askFor(t, memos[i]!, false, true);
      memos.splice(i, 1);
    }
    return memos[i]?.id ?? 0;
  }

  /**
   * The ask to the founder that comment `m` raised, kept as the comment reads now: an agent's comment that names `@human` has one open ask.
   * The founder writing on the task answers it.
   */
  private askFor(t: Row, m: Memo, adding: boolean, gone = false): void {
    const inbox = this.host.inbox;
    if (!this.isAgent(m.author)) return adding ? inbox.closeAsks(t.id) : undefined;
    const open = inbox.askOf(t.id, m.id);
    const want = !gone && NAMES_HUMAN.test(m.text);
    if (want && !open) inbox.add({ kind: 'ask', agent: m.author, title: m.text, body: m.text, task: t.id, memo: m.id });
    else if (want && open) inbox.retext(open, m.text);
    else if (open) inbox.close(open);
  }

  /** Where the tell of a founder's comment stands for each agent it was told to, as the daemon reports it (Memo.readers). */
  setReaders(task: number, memo: number, readers: MemoReader[]): void {
    const m = this.find(task).memos?.find((x) => x.id === memo);
    if (!m) throw new TaskFailure('refused', `memo ${memo} does not exist`);
    m.readers = readers;
  }

  /** task.comment.send_now: a held tell goes to the agent now. */
  sendNow(p: Record<string, unknown>): void {
    const row = this.rows.find((t) => t.memos?.some((m) => m.id === p.memo && m.readers?.some((r) => r.agent === p.agent)));
    const tell = row?.memos?.find((m) => m.id === p.memo)?.readers?.find((r) => r.agent === p.agent);
    if (!row || !tell || tell.state !== 'held') throw new TaskFailure('refused', 'that comment is not held for this agent');
    Object.assign(tell, { state: 'sent', at: this.now() });
    delete tell.minutes;
    this.emit(row);
  }

  /** A memo that carries files, as `hl task comment add --file` leaves it. */
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

  /** The task's comments, each cut at the images its text names (one page: the fake keeps no more). */
  comments(p: Record<string, unknown>, agent = false): { memos: Memo[]; more: boolean } {
    return { memos: this.get(p, agent).memos, more: false };
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

  // ---- the verbs of the daemon's task plan (D3), served only when the fake is asked to ----

  /** A done closes the task; a review is a task.handoff of the task itself. */
  done(p: Record<string, unknown>): string {
    const t = this.find(p.task);
    if (t.state !== 'doing' && t.state !== 'queued') throw new TaskFailure('refused', `task ${t.id} is ${t.state}, not open`);
    const evidence = String(p.evidence ?? '') || null;
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
    if (!String(p.reason ?? '').trim()) throw new TaskFailure('bad_request', 'a discard needs a reason');
    if (t.state === 'done' || t.state === 'discarded') throw new TaskFailure('refused', `task ${t.id} is ${t.state}`);
    this.change(t, { state: 'discarded', done: this.now(), hold_reason: String(p.reason) });
    this.host.inbox.closeAsks(t.id);
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
const BASIC = new Set([
  'tasks.list',
  'task.get',
  'task.comment.list',
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
