// The fake daemon's requests: the founder's requests.list rows, and task.request and task.none, which turn a message of theirs into a request or
// mark it as needing no work. A test or a seed puts rows in with `add`; the requests topic says when one changed.
import type { RequestRow, Timestamp } from '@handline/contract';
import { Failure, invalidParams } from '#src/errors.ts';

interface Host {
  emit(topic: 'requests', type: string, f: { agent?: string; data: Record<string, unknown> }): void;
  /** The chat, for a request named by the number of a message of the founder's. */
  chat: {
    find(id: unknown): { id: number; to: string; body: string; agent: string };
    change(m: never, patch: { request_task?: number | null; no_work?: string | null }): void;
  };
}
type Accepted = (said: string, extra?: object) => unknown;

const METHODS = /^(requests.list|task.request|task.none)$/;
const PAGE = 50;

export class FakeRequests {
  readonly rows: RequestRow[] = [];
  private host: Host;
  private now: () => Timestamp;
  private next = 9000;

  constructor(host: Host, now: () => Timestamp) {
    this.host = host;
    this.now = now;
  }

  owns = (name: string): boolean => METHODS.test(name);
  lists = (): boolean => true;

  /** A request row as the daemon reads it; what a test does not say is the plainest value. */
  add(row: Partial<RequestRow> & Pick<RequestRow, 'title'>): RequestRow {
    const message = row.kind === 'message' || (row.kind === undefined && row.task === undefined);
    const full: RequestRow = {
      kind: message ? 'message' : 'task',
      task: message ? null : (row.task ?? this.next++),
      ref: message ? `#${this.next++}` : null,
      status: message ? 'not_yet' : 'doing',
      agent: 'handline.architect',
      holders: [],
      open: 0,
      done: 0,
      prs: [],
      now: null,
      blocked: false,
      progress: '',
      reason: null,
      unread_comments: 0,
      created: this.now(),
      last_activity: this.now(),
      cursor: `c${String(9999 - this.rows.length).padStart(4, '0')}`,
      ...row,
    };
    this.rows.unshift(full);
    this.host.emit('requests', 'request.changed', { agent: full.agent, data: { ...full } });
    return full;
  }

  call(method: string, p: Record<string, unknown>, accepted: Accepted): unknown {
    if (method === 'requests.list') return this.list(p);
    const row = this.rows.find((r) => r.ref !== null && r.ref === String(p.request)) ?? this.fromChat(p.request);
    if (!row) throw new Failure('not_found', `no message ${String(p.request)} of the founder's`, { field: 'request' });
    const mark = (patch: { request_task?: number | null; no_work?: string | null }) => {
      const m = typeof row.ref === 'string' && row.ref.startsWith('msg:') ? this.host.chat.find(Number(row.ref.slice(4))) : null;
      if (m) this.host.chat.change(m as never, patch);
    };
    if (method === 'task.none') {
      if (typeof p.reason !== 'string' || !p.reason.trim()) throw invalidParams('reason says why no work is needed', 'reason');
      Object.assign(row, { status: 'no_work', reason: p.reason.trim() });
      mark({ no_work: p.reason.trim() });
      this.host.emit('requests', 'request.changed', { agent: row.agent, data: { ...row } });
      return accepted('Marked: no work.');
    }
    const existing = row.kind === 'task';
    if (!existing) {
      Object.assign(row, { kind: 'task', task: this.next++, status: 'doing', holders: [row.agent], origin: row.agent });
      if (typeof p.title === 'string' && p.title.trim()) row.title = p.title.trim();
      mark({ request_task: row.task });
      this.host.emit('requests', 'request.changed', { agent: row.agent, data: { ...row } });
    }
    return accepted(existing ? 'Already a request.' : 'Request made.', { task: row.task, ...(existing ? { existing: true } : {}) });
  }

  /** A request named by a chat message's number: its row is made on the first call. */
  private fromChat(name: unknown): RequestRow | undefined {
    if (typeof name !== 'number' && !/^\d+$/.test(String(name))) return undefined;
    try {
      const m = this.host.chat.find(Number(name));
      return this.add({ title: m.body.split('\n')[0]!.slice(0, 80), kind: 'message', agent: m.to, ref: `msg:${m.id}` });
    } catch {
      return undefined;
    }
  }

  private list(p: Record<string, unknown>) {
    const limit = typeof p.limit === 'number' ? p.limit : PAGE;
    const from = typeof p.before === 'string' ? this.rows.findIndex((r) => r.cursor === p.before) + 1 : 0;
    const rows = this.rows.filter((r) => p.state === undefined || r.status === p.state).slice(from, from + limit);
    const more = this.rows.filter((r) => p.state === undefined || r.status === p.state).length > from + limit;
    return { items: rows, more };
  }
}
