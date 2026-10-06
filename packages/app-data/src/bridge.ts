// The event bridge: what each pushed event does to the cache (docs/turborepo-layout.md §8.4). The daemon's event is the truth and
// always wins over an optimistic value, so every rule here replaces by identity and never merges a guess.
import type { QueryClient } from '@tanstack/react-query';
import type { Account, AgentDetail, AgentView, DaemonStatus, Event as WireEvent, InboxItem, Operation, Task } from '@handline/client';
import { edit, upsert, type Ctx } from './cache.ts';
import { chatEvent } from './chat-events.ts';
import { qk } from './reads.ts';

type Rule = (c: Ctx, e: WireEvent) => void;
type Data<T> = T & Record<string, unknown>;
const data = <T>(e: WireEvent) => e.data as unknown as Data<T>;

const rules: Record<string, Rule> = {
  'agent.changed': ({ qc, d }, e) => {
    const a = data<AgentView>(e);
    edit<{ agents: AgentView[] }>(qc, qk.agents(d), (l) => ({ ...l, agents: upsert(l.agents, a, (x) => x.name === a.name) }));
    edit<AgentDetail>(qc, qk.agent(d, a.name), (old) => ({ ...old, ...a }));
  },
  'agent.removed': ({ qc, d }, e) => {
    const name = e.agent ?? String(data<{ name: string }>(e).name);
    edit<{ agents: AgentView[] }>(qc, qk.agents(d), (l) => ({ ...l, agents: l.agents.filter((x) => x.name !== name) }));
    qc.removeQueries({ queryKey: qk.agent(d, name) });
  },
  // The store keeps the notes; the detail is read again, since a control changes what it shows.
  'agent.control': ({ qc, d }, e) => {
    if (e.agent) void qc.invalidateQueries({ queryKey: qk.agent(d, e.agent) });
  },
  // A filter makes a patch of a list unsafe, so the lists are read again.
  'task.changed': ({ qc, d }, e) => {
    const t = data<Task>(e);
    edit(qc, qk.task(d, t.id), (old: object) => ({ ...old, ...t }));
    void qc.invalidateQueries({ queryKey: qk.tasks(d) });
    void qc.invalidateQueries({ queryKey: ['d', d, 'board'] });
  },
  'inbox.added': inbox,
  'inbox.changed': inbox,
  'account.changed': ({ qc, d }, e) => {
    const a = data<Account>(e);
    edit<{ accounts: Account[] }>(qc, qk.accounts(d), (l) => ({ ...l, accounts: upsert(l.accounts, a, (x) => x.name === a.name) }));
  },
  'status.changed': ({ qc, d }, e) => {
    qc.setQueryData<DaemonStatus>(qk.status(d), data<DaemonStatus>(e));
  },
  'operation.progress': ({ qc, d }, e) => {
    const o = data<Operation>(e);
    qc.setQueryData<Operation>(qk.operation(d, o.id), o);
  },
  prompt_asked: ({ qc, d }, e) => {
    const agent = e.agent ?? String(data<{ agent: string }>(e).agent);
    void qc.invalidateQueries({ queryKey: qk.prompts(d, agent) });
    void qc.invalidateQueries({ queryKey: qk.agent(d, agent) });
  },
  'device.paired': ({ qc, d }) => void qc.invalidateQueries({ queryKey: ['d', d, 'devices'] }),
  'device.revoked': ({ qc, d }) => void qc.invalidateQueries({ queryKey: ['d', d, 'devices'] }),
};

/** An unfiltered list takes the item in place; a filtered one may or may not now hold it, so it is read again. */
function inbox({ qc, d }: Ctx, e: WireEvent) {
  const item = data<InboxItem>(e);
  for (const [key] of qc.getQueriesData({ queryKey: qk.inboxes(d) })) {
    if (key.length === qk.inboxes(d).length)
      edit<{ items: InboxItem[] }>(qc, key, (l) => ({ ...l, items: upsert(l.items, item, (x) => x.id === item.id, 'start') }));
    else void qc.invalidateQueries({ queryKey: key, exact: true });
  }
}

/** Notifications that only toast (task_done, stall, account_out) change no cache; the shell shows them from the event itself. */
export function applyEvent(c: Ctx, e: WireEvent): void {
  (rules[e.type] ?? (e.topic === 'chat' ? chatEvent : undefined))?.(c, e);
}

/** Everything the daemon described since the last cursor may have changed: every active query reads again. */
export const resync = (qc: QueryClient, d: string) => void qc.invalidateQueries({ queryKey: qk.all(d) });

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
