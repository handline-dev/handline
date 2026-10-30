// The event bridge: what each pushed event does to the cache (docs/turborepo-layout.md §8.4). The daemon's event is the truth and
// always wins over an optimistic value, so every rule here replaces by identity and never merges a guess.
import type { QueryClient } from '@tanstack/react-query';
import type {
  Proposal,
  Account,
  AgentDetail,
  AgentView,
  DaemonStatus,
  Event as WireEvent,
  InboxItem,
  Operation,
  Prefs,
  RequestRow,
  Task,
} from '@handline/client';
import { edit, inRead, put, upsert, type Ctx, type Pages } from '#src/cache.ts';
import { chatEvent } from '#src/chat-events.ts';
import { transcriptEvent } from '#src/transcript.ts';
import { qk } from '#src/reads.ts';

type Rule = (c: Ctx, e: WireEvent) => void;
type Data<T> = T & Record<string, unknown>;
const data = <T>(e: WireEvent) => e.data as unknown as Data<T>;

const rules: Record<string, Rule> = {
  'agent.changed': ({ qc, d }, e) => {
    const a = data<AgentView>(e);
    edit<{ agents: AgentView[] }>(qc, qk.agents(d), (l) => ({ ...l, agents: upsert(l.agents, a, (x) => x.name === a.name) }), e.cursor);
    edit<AgentDetail>(qc, qk.agent(d, a.name), (old) => ({ ...old, ...a }), e.cursor);
    // The row does not carry what the detail adds (terminal, who may type, the open prompt), so an open detail is read again.
    void qc.invalidateQueries({ queryKey: qk.agent(d, a.name) });
  },
  'agent.removed': ({ qc, d }, e) => {
    const name = e.agent ?? String(data<{ name: string }>(e).name);
    edit<{ agents: AgentView[] }>(qc, qk.agents(d), (l) => ({ ...l, agents: l.agents.filter((x) => x.name !== name) }), e.cursor);
    qc.removeQueries({ queryKey: qk.agent(d, name) });
  },
  // The store keeps the notes; the detail is read again, since a control changes what it shows.
  'agent.control': ({ qc, d }, e) => {
    if (!e.agent) return;
    void qc.invalidateQueries({ queryKey: qk.agent(d, e.agent) });
    void qc.invalidateQueries({ queryKey: qk.resource(d, 'agent.timeline') });
  },
  // Each cached list takes the task in place, out or in by its own filter; only a list the patch cannot judge is read again.
  'task.changed': ({ qc, d }, e) => {
    const t = data<Task>(e);
    edit(qc, qk.task(d, t.id), (old: object) => ({ ...old, ...t }), e.cursor);
    // The row carries no body, so an edited brief only shows when the open detail is read again.
    void qc.invalidateQueries({ queryKey: qk.task(d, t.id) });
    void qc.invalidateQueries({ queryKey: qk.taskHistory(d, t.id) });
    patchTaskLists(qc, d, t, e.cursor);
    void qc.invalidateQueries({ queryKey: ['d', d, 'board'] });
    // A message's one-tap actions follow its task's state, so the chat is read again.
    void qc.invalidateQueries({ queryKey: ['d', d, 'chat-messages'] });
  },
  // A deleted image is named in chats, task texts and comments: each is read again, and the draw says "Photo deleted".
  'attachment.removed': ({ qc, d }) => {
    for (const k of ['chat-messages', 'memos', 'task']) void qc.invalidateQueries({ queryKey: ['d', d, k] });
  },
  'inbox.added': inbox,
  'inbox.changed': inbox,
  'account.changed': ({ qc, d }, e) => {
    const a = data<Account>(e);
    // The topic row has no usage fields; the usage read's cache (also under this key) keeps the ones it has.
    edit<{ accounts: Account[] }>(
      qc,
      qk.accounts(d),
      (l) => ({
        ...l,
        accounts: upsert(l.accounts, { ...l.accounts.find((x) => x.name === a.name), ...a }, (x) => x.name === a.name),
      }),
      e.cursor,
    );
  },
  'status.changed': ({ qc, d }, e) => {
    put<DaemonStatus>(qc, qk.status(d), data<DaemonStatus>(e), e.cursor);
  },
  'operation.progress': ({ qc, d }, e) => {
    const o = data<Operation>(e);
    put<Operation>(qc, qk.operation(d, o.id), o, e.cursor);
  },
  prompt_asked: ({ qc, d }, e) => {
    const agent = e.agent ?? String(data<{ agent: string }>(e).agent);
    void qc.invalidateQueries({ queryKey: qk.prompts(d, agent) });
    void qc.invalidateQueries({ queryKey: qk.agent(d, agent) });
  },
  'hr.raised': hr,
  'hr.settled': hr,
  'device.paired': ({ qc, d }) => void qc.invalidateQueries({ queryKey: ['d', d, 'devices'] }),
  'device.revoked': ({ qc, d }) => void qc.invalidateQueries({ queryKey: ['d', d, 'devices'] }),
  'prefs.changed': ({ qc, d }, e) => put<Prefs>(qc, qk.prefs(d), data<Prefs>(e), e.cursor),
  // The row takes its place in every cached requests list (a message that became a request keeps its ref); a list is newest first, so a new one goes on top.
  'request.changed': ({ qc, d }, e) => {
    const row = data<RequestRow>(e);
    const same = (x: RequestRow) => (row.ref !== null && x.ref === row.ref) || (row.task !== null && x.task === row.task);
    for (const [key, page] of qc.getQueriesData<{ items: RequestRow[]; more: boolean }>({ queryKey: qk.resource(d, 'requests.list') })) {
      if (!page || inRead(page, e.cursor)) continue;
      const items = page.items.some(same) ? page.items.map((x) => (same(x) ? row : x)) : [row, ...page.items];
      qc.setQueryData(key, { ...page, items });
    }
  },
};

type TaskFilter = {
  owner?: string;
  from?: string;
  state?: string;
  parent?: number;
  under?: number;
  before?: number;
  all?: boolean;
  limit?: number;
};
type TaskPage = { items: Task[]; more?: boolean };
const isPage = (x: unknown): x is TaskPage => typeof x === 'object' && x !== null && Array.isArray((x as TaskPage).items);

/** Whether a list read with `f` holds `t`: the default list is the open tasks, `all` adds the done and discarded. */
function listHolds(f: TaskFilter, t: Task): boolean {
  const closed = t.state === 'done' || t.state === 'discarded';
  return (
    (f.all || f.state !== undefined || !closed) &&
    (f.owner === undefined || f.owner === t.to) &&
    (f.from === undefined || f.from === t.from) &&
    (f.state === undefined || f.state === t.state) &&
    (f.parent === undefined || f.parent === t.parent)
  );
}

/** Lists are newest first by number. A task that now belongs past the end of a full page is left to the next page. */
function patchTaskLists(qc: QueryClient, d: string, t: Task, cursor: number): void {
  for (const [key, page] of qc.getQueriesData({ queryKey: qk.tasks(d) })) {
    const f = (key[3] ?? {}) as TaskFilter;
    if (page === undefined || inRead(page, cursor)) continue;
    if (!isPage(page) || f.under !== undefined || f.before !== undefined) {
      void qc.invalidateQueries({ queryKey: key, exact: true });
      continue;
    }
    const has = page.items.some((x) => x.id === t.id);
    const holds = listHolds(f, t);
    if (!has && !holds) continue;
    let items = page.items;
    if (has) items = holds ? items.map((x) => (x.id === t.id ? { ...x, ...t } : x)) : items.filter((x) => x.id !== t.id);
    else {
      const last = items[items.length - 1];
      if (page.more && last && t.id < last.id) continue;
      items = [...items.filter((x) => x.id > t.id), t, ...items.filter((x) => x.id < t.id)];
    }
    const limit = f.limit ?? 50;
    const more = page.more || items.length > limit;
    qc.setQueryData(key, { ...page, items: items.slice(0, limit), more });
  }
}

/** A proposal takes its place in the unfiltered list at once (the dot's count follows); a filtered list is read again. */
function hr({ qc, d }: Ctx, e: WireEvent) {
  const p = data<{ proposal: Proposal }>(e).proposal;
  for (const [key] of qc.getQueriesData({ queryKey: qk.hrs(d) })) {
    if (key.length === qk.hrs(d).length)
      edit<{ proposals: Proposal[] }>(
        qc,
        key,
        (l) => ({ ...l, proposals: upsert(l.proposals, p, (x) => x.id === p.id, 'start') }),
        e.cursor,
      );
    else void qc.invalidateQueries({ queryKey: key, exact: true });
  }
}

/** An unfiltered list takes the item in place; a filtered one may or may not now hold it, so it is read again. */
function inbox({ qc, d }: Ctx, e: WireEvent) {
  const item = data<InboxItem>(e);
  // agent.changed carries no last_to_founder or founder_unread, so the agent rows are read again.
  void qc.invalidateQueries({ queryKey: qk.agents(d) });
  for (const [key] of qc.getQueriesData({ queryKey: qk.inboxes(d) })) {
    if (key.length === qk.inboxes(d).length)
      edit<{ items: InboxItem[] }>(qc, key, (l) => ({ ...l, items: upsert(l.items, item, (x) => x.id === item.id, 'start') }), e.cursor);
    else void qc.invalidateQueries({ queryKey: key, exact: true });
  }
}

/** Notifications that only toast (task_done, stall, account_out) change no cache; the shell shows them from the event itself. */
export function applyEvent(c: Ctx, e: WireEvent): void {
  (rules[e.type] ?? (e.topic === 'chat' ? chatEvent : e.topic === 'transcript' ? transcriptEvent : undefined))?.(c, e);
}

/** A gap in the stream: everything the daemon described since the last cursor may have changed, so every active query reads again. */
export const resync = (qc: QueryClient, d: string) => void qc.invalidateQueries({ queryKey: qk.all(d) });

/**
 * The connection came back and the stream restarts at the newest event, so only what is open reads again (a query nobody watches is marked
 * stale and read when it opens). A chat holds its older pages only for a person who is reading up there (`held`); otherwise it drops to its first page.
 */
export function resume(qc: QueryClient, d: string, held: ReadonlySet<string>): void {
  for (const resource of ['chat-messages', 'chat-transcript']) {
    for (const [key, data] of qc.getQueriesData<Pages<unknown>>({ queryKey: ['d', d, resource] })) {
      if (data && data.pages.length > 1 && !held.has(String(key[3])))
        qc.setQueryData(key, { pages: data.pages.slice(0, 1), pageParams: data.pageParams.slice(0, 1) });
    }
  }
  resync(qc, d);
}

/** Collects events and applies them together in a microtask, as the store batches detail reloads. */
export function createBridge(qc: QueryClient, d: string, me: () => string) {
  let queue: WireEvent[] = [];
  const flush = () => {
    const batch = queue;
    queue = [];
    for (const e of batch) applyEvent({ qc, d, me: me(), now: e.at }, e);
  };
  return {
    push(e: WireEvent) {
      queue.push(e);
      if (queue.length === 1) queueMicrotask(flush);
    },
    flush,
  };
}
