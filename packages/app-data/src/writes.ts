// Every write method of the contract, once, with its kind and what it does to the cache (docs/turborepo-layout.md §8.5).
// A method in none of the four lists fails the guard test, so a new write is decided by a person before it ships.
import { taskAfter, type TaskEdit, type TaskVerb } from '@handline/app-core';
import type { Account, InboxItem, Memo, Proposal, Schedule, Settings, StandingAnswer, Task } from '@handline/client';
import { edit, provisionalId, upsert, type Ctx } from './cache.ts';
import { accountAfter, type AccountChange } from './account-after.ts';
import { accountRemove } from './account-remove.ts';
import { chatWrites } from './chat-writes.ts';
import { qk } from './reads.ts';
import type { WriteSpec } from './write-spec.ts';

const as = <T>(p: never) => p as T;
const decided = (label: string): WriteSpec => ({ kind: 'decided', label });
const uncached = (label: string): WriteSpec => ({ kind: 'uncached', label });

type List<K extends string, T> = Record<K, T[]>;
const listOf =
  <K extends string, T>(key: K, f: (items: T[]) => T[]) =>
  (l: List<K, T>) => ({ ...l, [key]: f(l[key]) });

// ---- tasks ----

const taskKeys = (d: string, p: never) => {
  const { task, tasks } = as<{ task?: number; tasks?: number[] }>(p);
  return [qk.tasks(d), ...[...(task === undefined ? [] : [task]), ...(tasks ?? [])].map((t) => qk.task(d, t))];
};
const changeTasks = (c: Ctx, ids: number[], f: (t: Task) => Task | undefined) => {
  const one = (t: Task) => (ids.includes(t.id) ? (f(t) ?? t) : t);
  edit<List<'items', Task>>(
    c.qc,
    qk.tasks(c.d),
    listOf('items', (l) => l.map(one)),
  );
  for (const id of ids) edit<Task>(c.qc, qk.task(c.d, id), one);
};
const verb = (name: TaskVerb, label: string): WriteSpec => ({
  kind: 'authored',
  label,
  touches: taskKeys,
  patch: (c, p) => {
    const { task, tasks, owner, title, time_box, priority } = as<{ task?: number; tasks?: number[] } & TaskEdit>(p);
    changeTasks(c, tasks ?? [task!], (t) => taskAfter(name, t, c.now, { owner, title, time_box, priority }));
  },
});

/** A provisional row's own id is replaced by the daemon's number, or the row goes if the daemon's own event already added the real one. */
function renumber<T extends { id: number }>(rows: T[], provisional: (r: T) => boolean, id: number): T[] {
  const real = rows.some((r) => r.id === id);
  return rows.flatMap((r) => (provisional(r) ? (real ? [] : [{ ...r, id }]) : [r]));
}

const newTask = (c: Ctx, p: never): Task => {
  const v = as<{ owner: string; title: string; time_box?: string; relation?: string; response?: string; parent?: number }>(p);
  return {
    id: provisionalId(),
    from: c.me,
    to: v.owner,
    title: v.title,
    state: 'pending',
    relation: v.relation ?? 'manager',
    response: v.response ?? 'async',
    time_box: v.time_box ?? null,
    parent: v.parent ?? null,
    created: c.now,
    delivered: null,
    done: null,
    hold_reason: null,
    block_reason: null,
    evidence: null,
  };
};

const tasks: Record<string, WriteSpec> = {
  'task.new': {
    kind: 'authored',
    label: 'new task',
    touches: (d) => [qk.tasks(d)],
    patch: (c, p) =>
      edit<List<'items', Task>>(
        c.qc,
        qk.tasks(c.d),
        listOf('items', (l) => [newTask(c, p), ...l]),
      ),
    reconcile: (c, _p, r) => {
      const id = (r as { task: number }).task;
      edit<List<'items', Task>>(
        c.qc,
        qk.tasks(c.d),
        listOf('items', (l) => renumber(l, (t) => t.id < 0, id)),
      );
    },
  },
  'task.done': verb('done', 'done'),
  'task.reopen': verb('reopen', 'reopen'),
  'task.queue': verb('queue', 'queue'),
  'task.draft': verb('draft', 'draft'),
  'task.release': verb('release', 'release'),
  'task.withdraw': verb('withdraw', 'withdraw'),
  'task.cancel': verb('cancel', 'cancel'),
  'task.edit': verb('edit', 'edit'),
  'task.merge': {
    kind: 'authored',
    label: 'merge',
    touches: (d) => [qk.tasks(d)],
    patch: (c, p) => {
      const gone = as<{ tasks: number[] }>(p).tasks;
      edit<List<'items', Task>>(
        c.qc,
        qk.tasks(c.d),
        listOf('items', (l) => l.filter((t) => !gone.includes(t.id))),
      );
    },
  },
  'task.memo.add': {
    kind: 'authored',
    label: 'add memo',
    touches: (d, p) => [qk.memos(d, as<{ task: number }>(p).task)],
    patch: (c, p) => {
      const { task, text } = as<{ task: number; text: string }>(p);
      const memo: Memo = { id: provisionalId(), task, author: c.me, at: c.now, text };
      edit<List<'memos', Memo>>(
        c.qc,
        qk.memos(c.d, task),
        listOf('memos', (l) => [...l, memo]),
      );
    },
    reconcile: (c, p, r) => {
      edit<List<'memos', Memo>>(
        c.qc,
        qk.memos(c.d, as<{ task: number }>(p).task),
        listOf('memos', (l) => renumber(l, (m) => m.id < 0, (r as { memo: number }).memo)),
      );
    },
  },
  'task.memo.edit': {
    kind: 'authored',
    label: 'edit memo',
    touches: (d, p) => [qk.memos(d, as<{ task: number }>(p).task)],
    patch: (c, p) => {
      const { task, memo, text } = as<{ task: number; memo: number; text: string }>(p);
      edit<List<'memos', Memo>>(
        c.qc,
        qk.memos(c.d, task),
        listOf('memos', (l) => l.map((m) => (m.id === memo ? { ...m, text } : m))),
      );
    },
  },
  'task.memo.delete': {
    kind: 'authored',
    label: 'delete memo',
    touches: (d, p) => [qk.memos(d, as<{ task: number }>(p).task)],
    patch: (c, p) => {
      const { task, memo } = as<{ task: number; memo: number }>(p);
      edit<List<'memos', Memo>>(
        c.qc,
        qk.memos(c.d, task),
        listOf('memos', (l) => l.filter((m) => m.id !== memo)),
      );
    },
  },
};

// ---- inbox, settings, accounts, schedules, standing answers ----

const inboxKeys = (d: string) => [qk.inboxes(d)];
const markItems = (c: Ctx, ids: number[], patch: Partial<InboxItem>) =>
  edit<List<'items', InboxItem>>(
    c.qc,
    qk.inboxes(c.d),
    listOf('items', (l) => l.map((i) => (ids.includes(i.id) ? { ...i, ...patch } : i))),
  );

const settings: Record<string, WriteSpec> = {
  'inbox.mark_read': {
    kind: 'authored',
    label: 'mark read',
    touches: inboxKeys,
    patch: (c, p) => markItems(c, as<{ ids: number[] }>(p).ids, { read: true }),
  },
  'inbox.answer': {
    kind: 'authored',
    label: 'answer',
    touches: inboxKeys,
    patch: (c, p) => markItems(c, [as<{ id: number }>(p).id], { read: true, answered: true }),
  },
  'inbox.reply': {
    kind: 'authored',
    label: 'reply',
    touches: inboxKeys,
    patch: (c, p) => markItems(c, [as<{ id: number }>(p).id], { read: true }),
  },
  'agent.set': {
    kind: 'authored',
    label: 'set',
    touches: (d, p) => [qk.agent(d, as<{ agent: string }>(p).agent)],
    patch: (c, p) => {
      const { agent, values } = as<{ agent: string; values: object }>(p);
      edit<{ settings?: object }>(c.qc, qk.agent(c.d, agent), (a) => ({ ...a, settings: { ...a.settings, ...values } }));
    },
  },
  'settings.set': {
    kind: 'authored',
    label: 'save settings',
    touches: (d) => [['d', d, 'settings']],
    // A per-agent override is not the shared value, so only the shared one is shown early.
    patch: (c, p) => {
      const { agent, changes } = as<{ agent?: string; changes: object }>(p);
      if (!agent) edit<Settings>(c.qc, ['d', c.d, 'settings'], (s) => ({ ...s, values: { ...s.values, ...changes } }));
    },
  },
  'trust.remove': {
    kind: 'authored',
    label: 'remove folder',
    touches: (d) => [['d', d, 'trust']],
    patch: (c, p) =>
      edit<List<'roots', string>>(
        c.qc,
        ['d', c.d, 'trust'],
        listOf('roots', (l) => l.filter((r) => r !== as<{ root: string }>(p).root)),
      ),
  },
  'account.set': {
    kind: 'authored',
    label: 'save account',
    touches: (d) => [qk.accounts(d)],
    patch: (c, p) => {
      const { account } = as<{ account: string }>(p);
      edit<List<'accounts', Account>>(
        c.qc,
        qk.accounts(c.d),
        listOf('accounts', (l) => l.map((a) => (a.name === account ? accountAfter(a, as<AccountChange>(p)) : a))),
      );
    },
  },
};

const scheduleKeys = (d: string) => [['d', d, 'schedules'] as const];
const editSchedules = (c: Ctx, f: (l: Schedule[]) => Schedule[]) =>
  edit<List<'schedules', Schedule>>(c.qc, ['d', c.d, 'schedules'], listOf('schedules', f));
const standing = (p: never): StandingAnswer => {
  const { folder, vendor, title, label } = as<{ folder?: string; vendor?: string; title?: string; label?: string }>(p);
  return folder
    ? { kind: 'trust', key: [folder], text: folder }
    : { kind: 'dialog', key: [vendor ?? '', title ?? '', label ?? ''], text: label ?? '' };
};

const schedules: Record<string, WriteSpec> = {
  'schedule.add': {
    kind: 'authored',
    label: 'add schedule',
    touches: scheduleKeys,
    patch: (c, p) => {
      const { target, cron, message } = as<{ target: string; cron: string; message: string }>(p);
      editSchedules(c, (l) => [...l, { id: provisionalId(), target, cron, message, next_run: null, last_run: null, set_by: c.me }]);
    },
    reconcile: (c, _p, r) => editSchedules(c, (l) => renumber(l, (s) => s.id < 0, (r as { schedule: number }).schedule)),
  },
  'schedule.edit': {
    kind: 'authored',
    label: 'edit schedule',
    touches: scheduleKeys,
    patch: (c, p) => {
      const { schedule, target, cron, message } = as<Partial<Schedule> & { schedule: number }>(p);
      const set = { ...(target ? { target } : {}), ...(cron ? { cron } : {}), ...(message ? { message } : {}) };
      editSchedules(c, (l) => l.map((s) => (s.id === schedule ? { ...s, ...set } : s)));
    },
  },
  'schedule.remove': {
    kind: 'authored',
    label: 'remove schedule',
    touches: scheduleKeys,
    patch: (c, p) => editSchedules(c, (l) => l.filter((s) => s.id !== as<{ schedule: number }>(p).schedule)),
  },
  'answer.add': {
    kind: 'authored',
    label: 'add standing answer',
    touches: (d) => [['d', d, 'answers']],
    patch: (c, p) =>
      edit<List<'answers', StandingAnswer>>(
        c.qc,
        ['d', c.d, 'answers'],
        listOf('answers', (l) => [...l, standing(p)]),
      ),
  },
  'answer.remove': {
    kind: 'authored',
    label: 'remove standing answer',
    touches: (d) => [['d', d, 'answers']],
    patch: (c, p) => {
      const { kind, key } = as<{ kind: string; key: string[] }>(p);
      edit<List<'answers', StandingAnswer>>(
        c.qc,
        ['d', c.d, 'answers'],
        listOf('answers', (l) => l.filter((a) => !(a.kind === kind && a.key.join('\0') === key.join('\0')))),
      );
    },
  },
};

// ---- decided: the cache is not touched; the view shows a pending mark and, on a refusal, the reason ----

const decidedWrites: Record<string, WriteSpec> = Object.fromEntries(
  Object.entries({
    'session.start': 'start',
    'session.wake': 'wake',
    'session.restart': 'restart',
    'session.park': 'park',
    'session.stop': 'stop',
    'session.interrupt': 'interrupt',
    'agent.fold': 'fold',
    'agent.move': 'move',
    'agent.unstall': 'clear stall',
    'input.take': 'take control',
    'input.release': 'release',
    'hr.withdraw': 'withdraw',
    'chat.seen': 'mark seen',
    'account.login': 'log in',
    'terminal.close': 'close terminal',
    'task.discard': 'discard',
    'device.pair': 'pair',
    'device.revoke': 'revoke',
  }).map(([m, label]) => [m, decided(label)]),
);

export const writes: Record<string, WriteSpec> = {
  'account.remove': accountRemove,
  ...chatWrites,
  ...tasks,
  ...settings,
  ...schedules,
  ...decidedWrites,
  // A prompt the person answers: a stale one is refused and the card shows the prompt that is open now.
  'prompt.answer': {
    kind: 'decided',
    label: 'answer',
    fail: (c, p, e) => {
      const current = (e as { code?: string; data?: { current?: unknown } }).data?.current;
      if ((e as { code?: string }).code !== 'stale_prompt') return false;
      const agent = as<{ agent: string }>(p).agent;
      c.qc.setQueryData(qk.prompts(c.d, agent), { prompts: current ? [current] : [] });
      edit<{ prompt: unknown }>(c.qc, qk.agent(c.d, agent), (d) => ({ ...d, prompt: current ?? null }));
      return true;
    },
  },
  // A decide on a proposal that changed or settled meanwhile: the refusal carries the proposal as it is now, and the card shows that.
  'hr.decide': {
    kind: 'decided',
    label: 'decide',
    fail: (c, _p, e) => {
      void c.qc.invalidateQueries({ queryKey: qk.hrs(c.d) });
      const proposal = (e as { data?: { proposal?: Proposal } }).data?.proposal;
      if (!proposal) return false;
      edit<{ proposals: Proposal[] }>(c.qc, ['d', c.d, 'hr'], (l) => ({
        ...l,
        proposals: upsert(l.proposals, proposal, (x) => x.id === proposal.id),
      }));
      return true;
    },
  },
  'session.input': uncached('type'),
  'session.keys': uncached('key'),
  'session.type': uncached('type'),
  'terminal.input': uncached('key'),
  'terminal.resize': uncached('resize'),
};

/** What a structured agent has no terminal for: a write of these to one is a bug in the view, not a refusal to wait for. */
export const NOT_FOR_STRUCTURED = ['session.input', 'session.keys', 'session.type', 'input.take', 'input.release'] as const;

/** The pairing write runs before hello, from the pair flow, outside the cache. */
export const PAIRING = ['device.redeem'] as const;

/** Agent commands: an agent sends these, the UI never does. Guard test 2 reads this list. */
export const NOT_FROM_UI = [
  'say',
  'land',
  'land.authorize',
  'land.pass',
  'hook.record',
  'hr.propose',
  'trust.propose',
  'task.begin',
  'task.start',
  'task.progress',
  'task.handoff',
  // A member command the app does not send yet.
  'task.ask',
] as const;
