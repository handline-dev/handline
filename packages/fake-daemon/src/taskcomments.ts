// The fake daemon's task comments: the memos on a row, the asks to the founder they raise, and who was told.
import type { Memo, MemoFile, MemoReader } from '@handline/contract';
import { refs } from '#src/attachments.ts';
import { TaskFailure, TaskStore, type Row } from '#src/taskstore.ts';

/** `@human` in a comment: at the start or after a character that is not part of a name. */
const NAMES_HUMAN = /(?:^|[^A-Za-z0-9._@-])@human(?:[^A-Za-z0-9_-]|$)/;

export class TaskComments extends TaskStore {
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
  protected askFor(t: Row, m: Memo, adding: boolean, gone = false): void {
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
  /** How many briefs and memos name image id, or carry it as a memo's file: attachment.remove's count. */
  naming(id: string): number {
    let n = 0;
    for (const t of this.rows) {
      if (refs(t.body).includes(id)) n++;
      for (const m of t.memos ?? []) if (refs(m.text).includes(id) || m.files?.some((f) => f.id === id)) n++;
    }
    return n;
  }
  /** Every memo's files through mark, so one the founder removed reads gone and removed. */
  remark(mark: (f: MemoFile) => MemoFile): void {
    for (const t of this.rows) for (const m of t.memos ?? []) if (m.files) m.files = m.files.map(mark);
  }

  /** The task's comments, each cut at the images its text names (one page: the fake keeps no more). */
  comments(p: Record<string, unknown>, agent = false): { memos: Memo[]; more: boolean } {
    return { memos: this.get(p, agent).memos, more: false };
  }
}
