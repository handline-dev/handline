// The fake daemon's inbox: results, decisions, blocks and prompts for the founder, newest first, on the inbox topic.
import type { InboxItem, Timestamp } from '@handline/contract';
import { Failure, refused } from './errors.ts';
import { seedInbox } from './fixtures.ts';

/** What the inbox needs of the daemon that hosts it: to emit on its topic. */
interface Host {
  emit(topic: 'inbox', type: string, f: { agent?: string; title?: string; data: Record<string, unknown> }): void;
}

export class FakeInbox {
  private items: InboxItem[] = [];
  private nextId = 1;
  private host: Host;
  private now: () => Timestamp;
  private on: boolean;

  /** `on` false is a daemon that does not serve the inbox: it lists none of its methods and holds no items. */
  constructor(host: Host, now: () => Timestamp, on: boolean) {
    this.host = host;
    this.now = now;
    this.on = on;
    if (on) seedInbox(this, now());
  }

  /** Whether the daemon lists this method. */
  lists = (name: string): boolean => this.on || !name.startsWith('inbox.');

  private emit(type: 'inbox.added' | 'inbox.changed', item: InboxItem) {
    this.host.emit('inbox', type, { agent: item.agent ?? undefined, title: item.title, data: item as unknown as Record<string, unknown> });
  }

  /** One inbox method; `accepted` makes the reply of a write. */
  call(method: string, p: Record<string, unknown>, who: string, accepted: (said: string) => unknown): unknown {
    switch (method) {
      case 'inbox.list':
        return this.list(p);
      case 'inbox.get':
        return this.get(p.id);
      case 'inbox.mark_read':
        return (this.markRead(p), accepted('Marked read.'));
      case 'inbox.answer':
        return (this.answer(p, who), accepted('Answered.'));
      case 'inbox.reply':
        return (this.reply(p), accepted('Sent.'));
      default:
        throw refused('unsupported', `the fake daemon does not serve ${method}`);
    }
  }

  unread(): number {
    return this.items.filter((i) => !i.read).length;
  }

  /** Puts a new item at the top and announces it. */
  add(item: Partial<InboxItem> & { kind: string; title: string }): InboxItem {
    const full: InboxItem = {
      id: this.nextId++,
      agent: null,
      body: '',
      evidence: null,
      task: null,
      created: this.now(),
      read: false,
      answered: false,
      options: [],
      prompt: null,
      ...item,
    };
    this.items.unshift(full);
    this.emit('inbox.added', { ...full });
    return full;
  }

  list(p: Record<string, unknown>): { items: InboxItem[]; more: boolean } {
    const limit = typeof p.limit === 'number' ? p.limit : 50;
    const rows = this.items.filter(
      (i) =>
        (p.unread !== true || !i.read) &&
        (typeof p.kind !== 'string' || i.kind === p.kind) &&
        (typeof p.agent !== 'string' || i.agent === p.agent) &&
        (typeof p.before !== 'number' || i.id < p.before),
    );
    return { items: rows.slice(0, limit).map((i) => ({ ...i })), more: rows.length > limit };
  }

  get(id: unknown): InboxItem {
    return { ...this.find(id) };
  }

  markRead(p: Record<string, unknown>): void {
    const ids = Array.isArray(p.ids) ? p.ids : [];
    for (const id of ids) this.change(this.find(id), { read: true });
  }

  answer(p: Record<string, unknown>, who: string): void {
    const item = this.find(p.id);
    if (item.answered) throw refused('state', `Already answered by ${who}.`);
    if (typeof p.option === 'string' && !item.options.some((o) => o.id === p.option))
      throw new Failure('bad_request', `no option ${p.option}`, { field: 'option' });
    this.change(item, { read: true, answered: true });
  }

  reply(p: Record<string, unknown>): void {
    this.change(this.find(p.id), { read: true });
  }

  /** An item is a request under the same id, so `chat.dismiss` takes it off the list; false when this inbox does not hold it. */
  dismiss = (id: unknown): boolean => {
    const at = this.items.findIndex((i) => i.id === id);
    if (at >= 0) this.items.splice(at, 1);
    return at >= 0;
  };

  private find(id: unknown): InboxItem {
    const item = this.items.find((i) => i.id === id);
    if (!item) throw new Failure('not_found', `no inbox item ${String(id)}`);
    return item;
  }
  private change(item: InboxItem, patch: Partial<InboxItem>) {
    Object.assign(item, patch);
    this.emit('inbox.changed', { ...item });
  }
}
