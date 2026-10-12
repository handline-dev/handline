import { act } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { setup } from '@handline/fake-daemon/helpers';
import { Store } from '@handline/app-core';
import { RpcError } from '@handline/client';
import {
  NOT_FOR_STRUCTURED,
  makeQueryClient,
  qk,
  useAgents,
  useChat,
  useChatActions,
  usePending,
  useWrite,
  writes,
  type Key,
  type WriteMethod,
} from '../src/index.ts';
import { DAEMON as D, mount, openStore, probe, refuse, sleep, stubClient, unmount, until } from './harness.tsx';

afterEach(unmount);
const ARCH = 'handline.architect';
const PARKED = 'handline.term-build';

// ---- the cache each authored row starts from, and the params it is called with ----

const msg = (id: number, over: object = {}) => ({
  id,
  at: 't',
  state_at: 't',
  from: 'me',
  to: 'a',
  state: 'queued',
  landing: 'queued',
  sending_reason: null,
  body: `m${id}`,
  answer: null,
  dismissed: false,
  delivered_at: null,
  typed_at: null,
  landed_at: null,
  parent: null,
  kind: 'message',
  tasks: [],
  is_task_question: false,
  ...over,
});
const chatSeed: [Key, unknown][] = [
  [qk.chatMessages(D, 'a', 'mine'), { pages: [{ messages: [msg(5)], more: false }], pageParams: [undefined] }],
  [qk.chatMessages(D, 'a', 'all'), { pages: [{ messages: [msg(5)], more: false }], pageParams: [undefined] }],
];
const task = (id: number, state: string) => ({
  id,
  from: 'founder',
  to: 'a',
  title: `t${id}`,
  state,
  relation: 'manager',
  response: 'async',
  time_box: null,
  parent: null,
  created: 't',
  delivered: null,
  done: null,
  hold_reason: null,
  block_reason: null,
  evidence: null,
});
const taskSeed: [Key, unknown][] = [
  [[...qk.tasks(D), { state: 'open' }], { items: [task(7, 'doing'), task(8, 'draft'), task(9, 'queued')], more: false }],
  [qk.task(D, 7), { ...task(7, 'doing'), body: 'b' }],
  [qk.task(D, 8), { ...task(8, 'draft'), body: 'b' }],
  [qk.task(D, 9), { ...task(9, 'queued'), body: 'b' }],
];
const inboxItem = (id: number) => ({
  id,
  kind: 'decision',
  agent: 'a',
  title: 'x',
  body: '',
  evidence: null,
  task: null,
  created: 't',
  read: false,
  answered: false,
  options: [],
  prompt: null,
});
const inboxSeed: [Key, unknown][] = [[qk.inbox(D), { items: [inboxItem(1), inboxItem(2)], more: false }]];
const memoSeed: [Key, unknown][] = [[qk.memos(D, 7), { memos: [{ id: 1, task: 7, author: 'me', at: 't', text: 'old' }] }]];
const account = { name: 'x', label: 'x', cap: null, cap_effective: 3, recover: false };
const settingsSeed: [Key, unknown][] = [[['d', D, 'settings'], { values: { idle_close: '5' }, trust_folders: [], overrides: {} }]];
const schedSeed: [Key, unknown][] = [
  [
    ['d', D, 'schedules'],
    { schedules: [{ id: 3, target: 'a', cron: '* * * * *', message: 'hi', next_run: null, last_run: null, set_by: 'me' }] },
  ],
];
const answersSeed: [Key, unknown][] = [[['d', D, 'answers'], { answers: [{ kind: 'trust', key: ['/w'], text: '/w' }] }]];

/** [method, params, the cache it starts from, whether the cache must differ at once]. A verb the daemon decides leaves it as it was. */
const AUTHORED: [string, object, [Key, unknown][], boolean][] = [
  ['chat.send', { agent: 'a', body: 'hello' }, chatSeed, true],
  ['chat.dismiss', { request: 5 }, chatSeed, true],
  ['task.new', { owner: 'a', title: 'new', body: '', time_box: '1h' }, taskSeed, true],
  ['task.done', { task: 7, evidence: 'ok' }, taskSeed, true],
  ['task.queue', { tasks: [8] }, taskSeed, true],
  ['task.draft', { tasks: [9] }, taskSeed, true],
  ['task.edit', { task: 7, title: 'renamed' }, taskSeed, true],
  ['task.reopen', { task: 7 }, taskSeed, false],
  ['task.release', { task: 7 }, taskSeed, false],
  ['task.withdraw', { task: 7 }, taskSeed, false],
  ['task.cancel', { task: 7 }, taskSeed, false],
  ['task.merge', { into: 7, tasks: [8, 9] }, taskSeed, true],
  ['task.memo.add', { task: 7, text: 'new' }, memoSeed, true],
  ['task.memo.edit', { task: 7, memo: 1, text: 'changed' }, memoSeed, true],
  ['task.memo.delete', { task: 7, memo: 1 }, memoSeed, true],
  ['inbox.mark_read', { ids: [1] }, inboxSeed, true],
  ['inbox.answer', { id: 1, option: 'yes' }, inboxSeed, true],
  ['inbox.reply', { id: 1, text: 'ok' }, inboxSeed, true],
  ['agent.set', { agent: 'a', values: { model: 'opus' } }, [[qk.agent(D, 'a'), { name: 'a', settings: { model: 'sonnet' } }]], true],
  ['settings.set', { changes: { idle_close: '9' } }, settingsSeed, true],
  ['trust.remove', { root: '/w' }, [[['d', D, 'trust'], { roots: ['/w'], proposals: [] }]], true],
  ['account.set', { account: 'x', label: 'Main', cap: 2 }, [[qk.accounts(D), { accounts: [account] }]], true],
  ['schedule.add', { target: 'a', cron: '0 * * * *', message: 'ping' }, schedSeed, true],
  ['schedule.edit', { schedule: 3, message: 'changed' }, schedSeed, true],
  ['schedule.remove', { schedule: 3 }, schedSeed, true],
  ['answer.add', { folder: '/w3' }, answersSeed, true],
  ['answer.remove', { kind: 'trust', key: ['/w'] }, answersSeed, true],
];

const clone = <T,>(x: T): T => structuredClone(x);
function deferred() {
  let reject!: (e: unknown) => void, resolve!: (v: unknown) => void;
  const promise = new Promise((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

describe('every authored write: the change is on screen before the daemon answers, and a refusal puts the cache back', () => {
  it('covers every authored row of the table', () => {
    const rows = AUTHORED.map((r) => r[0]).sort();
    expect(rows).toEqual(
      Object.entries(writes)
        .filter(([, s]) => s.kind === 'authored')
        .map(([m]) => m)
        .sort(),
    );
  });

  for (const [method, params, seed, changes] of AUTHORED) {
    it(method, async () => {
      const wait = deferred();
      const stub = stubClient(() => wait.promise);
      const store = openStore(stub);
      const w = probe(() => useWrite(method as WriteMethod, 'target'));
      const { qc } = await mount(store, w.node);
      for (const [key, data] of seed) qc.setQueryData(key, clone(data));
      const before = seed.map(([key]) => qc.getQueryData(key));
      act(() => w.get().mutate(params as never));
      await until(() => stub.count(method) === 1, 'the call');
      const during = seed.map(([key]) => qc.getQueryData(key));
      if (changes) expect(during, 'the edit shows at once').not.toEqual(before);
      else expect(during, 'a verb the daemon decides edits nothing').toEqual(before);
      expect(w.get().isPending).toBe(true);
      wait.reject(refuse('the daemon said no', 'state', 'try later'));
      await until(() => w.get().isError, 'the refusal');
      const after = seed.map(([key]) => qc.getQueryData(key));
      if (method === 'chat.send') expect(JSON.stringify(after)).toContain('"state":"failed"');
      else expect(after, 'the snapshot is back, deep equal').toEqual(before);
      expect(store.error).toMatch(new RegExp(`${writes[method]!.label}: the daemon said no \\(try later\\)`));
    });
  }
});

describe('writes', () => {
  it('a write that is accepted keeps its edit and clears the error line', async () => {
    const stub = stubClient(() => ({ accepted: true }));
    const store = openStore(stub);
    store.error = 'old';
    const w = probe(() => useWrite('inbox.mark_read'));
    const { qc } = await mount(store, w.node);
    qc.setQueryData(qk.inbox(D), { items: [inboxItem(1)], more: false });
    act(() => w.get().mutate({ ids: [1] }));
    await until(() => w.get().isSuccess, 'success');
    expect(qc.getQueryData<{ items: { read: boolean }[] }>(qk.inbox(D))!.items[0]!.read).toBe(true);
    expect(store.error).toBeNull();
  });

  it('hr.withdraw is a decided write from the UI: it calls the daemon, shows pending, and a refusal says why', async () => {
    const wait = deferred();
    const stub = stubClient(() => wait.promise);
    const store = openStore(stub);
    const w = probe(() => ({ write: useWrite('hr.withdraw', 'proposal'), pending: usePending('hr.withdraw') }));
    await mount(store, w.node);
    expect(writes['hr.withdraw']!.kind).toBe('decided');
    act(() => w.get().write.mutate({ proposal: 4 }));
    await until(() => stub.count('hr.withdraw') === 1, 'the call');
    expect(stub.calls.find((c) => c.method === 'hr.withdraw')!.params).toEqual({ proposal: 4 });
    expect(w.get().pending).toEqual([{ proposal: 4 }]);
    wait.reject(refuse('that proposal is already decided', 'state', 'it cannot be taken back'));
    await until(() => w.get().write.isError, 'the refusal');
    expect(w.get().pending).toEqual([]);
    expect(store.error).toMatch(/withdraw: that proposal is already decided \(it cannot be taken back\)/);
  });

  it("a refusal does not put an old value over an event that arrived while the write was in flight (the daemon's event wins)", async () => {
    const wait = deferred();
    const stub = stubClient(() => wait.promise);
    const w = probe(() => useWrite('task.done', 'target'));
    const { qc } = await mount(openStore(stub), w.node);
    for (const [key, data] of taskSeed) qc.setQueryData(key, clone(data));
    act(() => w.get().mutate({ task: 7, evidence: 'ok' }));
    await until(() => stub.count('task.done') === 1, 'the call');
    expect(qc.getQueryData<{ state: string }>(qk.task(D, 7))!.state).toBe('done');
    act(() => stub.emit({ type: 'task.changed', topic: 'tasks', data: task(7, 'blocked') }));
    await until(() => qc.getQueryData<{ state: string }>(qk.task(D, 7))!.state === 'blocked', 'the event');
    wait.reject(refuse('the daemon said no', 'state', 'try later'));
    await until(() => w.get().isError, 'the refusal');
    expect(qc.getQueryData<{ state: string }>(qk.task(D, 7))!.state).toBe('blocked');
  });

  it('a write is never retried, even when the connection drops', async () => {
    const stub = stubClient(() => {
      throw new RpcError('transport', 'connection lost');
    });
    const w = probe(() => useWrite('inbox.mark_read'));
    await mount(openStore(stub), w.node);
    act(() => w.get().mutate({ ids: [1] }));
    await until(() => w.get().isError, 'the failure');
    await sleep(50);
    expect(stub.count('inbox.mark_read')).toBe(1);
  });

  it('never sends a structured agent a terminal write: the five are refused before the wire, a terminal agent is not', async () => {
    const stub = stubClient();
    const qc = makeQueryClient();
    const row = (name: string, structured: boolean) => ({ name, structured });
    qc.setQueryData(qk.agents(D), { agents: [row('s.one', true), row('t.one', false)] });
    const five = NOT_FOR_STRUCTURED;
    expect(five).toEqual(['session.input', 'session.keys', 'session.type', 'input.take', 'input.release']);
    const w = probe(() => ({
      input: useWrite('session.input', 's.one'),
      keys: useWrite('session.keys', 's.one'),
      type: useWrite('session.type', 's.one'),
      take: useWrite('input.take', 's.one'),
      release: useWrite('input.release', 's.one'),
      mine: useWrite('input.take', 't.one'),
    }));
    await mount(openStore(stub), w.node, qc);
    act(() => {
      w.get().input.mutate({ agent: 's.one', text: 'x' } as never);
      w.get().keys.mutate({ agent: 's.one', keys: ['Esc'] } as never);
      w.get().type.mutate({ agent: 's.one', text: 'x' } as never);
      w.get().take.mutate({ agent: 's.one' } as never);
      w.get().release.mutate({ agent: 's.one' } as never);
      w.get().mine.mutate({ agent: 't.one' } as never);
    });
    await until(() => w.get().release.isError && w.get().mine.isSuccess, 'the guard');
    for (const m of five) expect(stub.count(m)).toBe(m === 'input.take' ? 1 : 0); // the one take is the terminal agent's
    expect(w.get().take.isError).toBe(true);
  });

  it('writes on one target run in order; writes on two targets do not wait for each other', async () => {
    const log: string[] = [];
    const stub = stubClient(async (m, p) => {
      log.push(`start ${p.agent}${p.n}`);
      await new Promise((r) => setTimeout(r, p.n === 1 ? 60 : 5));
      log.push(`end ${p.agent}${p.n}`);
      return {};
    });
    const a = probe(() => useWrite('session.interrupt', 'a'));
    const b = probe(() => useWrite('session.interrupt', 'b'));
    await mount(
      openStore(stub),
      <>
        {a.node}
        {b.node}
      </>,
    );
    act(() => {
      a.get().mutate({ agent: 'a', n: 1 } as never);
      a.get().mutate({ agent: 'a', n: 2 } as never);
      b.get().mutate({ agent: 'b', n: 2 } as never);
    });
    await until(() => log.includes('end a2'), 'both writes of a');
    expect(log.indexOf('end a1')).toBeLessThan(log.indexOf('start a2'));
    expect(log.indexOf('end b2')).toBeLessThan(log.indexOf('end a1'));
  });

  it('a stale prompt is refused, and the card gets the prompt that is open now', async () => {
    const current = { id: 'p2', agent: 'a', kind: 'permission', lines: [], options: [] };
    const stub = stubClient(() => {
      throw new RpcError('stale_prompt', 'stale', { code: 'stale_prompt', current });
    });
    const w = probe(() => useWrite('prompt.answer', 'a'));
    const { qc } = await mount(openStore(stub), w.node);
    qc.setQueryData(qk.prompts(D, 'a'), { prompts: [{ ...current, id: 'p1' }] });
    act(() => w.get().mutate({ agent: 'a', prompt: 'p1', choice: '1' }));
    await until(() => w.get().isError, 'the refusal');
    expect(qc.getQueryData(qk.prompts(D, 'a'))).toEqual({ prompts: [current] });
    expect(stub.count('prompt.answer')).toBe(1);
  });
});

async function connected() {
  const s = setup();
  const store = new Store(s.client);
  await store.start();
  return { ...s, store };
}

describe('a decided write, against the fake daemon with latency and a refusal', () => {
  it('leaves the cache alone while it waits, shows a pending mark, and on refusal the mark goes and the reason shows', async () => {
    const { store, daemon } = await connected();
    const v = probe(() => ({ agents: useAgents(), write: useWrite('session.start', PARKED), pending: usePending('session.start') }));
    const { qc } = await mount(store, v.node);
    await until(() => v.get().agents.data, 'the agents');
    const before = clone(qc.getQueryData(qk.agents(D)));
    daemon.knobs.latency(300);
    daemon.knobs.refuse('session.start', 'wall', 'the usage limit is hit', 'wait for the reset');
    act(() => v.get().write.mutate({ agent: PARKED }));
    await until(() => v.get().pending.length === 1, 'the pending mark');
    expect(qc.getQueryData(qk.agents(D))).toEqual(before);
    await until(() => v.get().write.isError, 'the refusal');
    expect(v.get().pending).toEqual([]);
    expect(qc.getQueryData(qk.agents(D))).toEqual(before);
    expect(store.error).toBe('start: the usage limit is hit (wait for the reset)');
  });
});

describe('chat send, against the fake daemon', () => {
  const view = () => probe(() => ({ chat: useChat(ARCH, 'mine'), act: useChatActions(ARCH) }));
  const mine = (c: { messages: { body: string; id: number; state: string; sending_reason: string | null }[] }, body: string) =>
    c.messages.filter((m) => m.body === body);

  it("shows the row at once, then the daemon's row replaces it: one row, a real id", async () => {
    const { store, daemon } = await connected();
    const v = view();
    await mount(store, v.node);
    await until(() => v.get().chat.status === 'ready', 'the conversation');
    daemon.knobs.latency(400);
    act(() => v.get().act.send('hello there'));
    await until(() => mine(v.get().chat, 'hello there').length === 1, 'the row before the daemon answers');
    expect(mine(v.get().chat, 'hello there')[0]!.id).toBeLessThan(0);
    await until(() => mine(v.get().chat, 'hello there')[0]!.id > 0, "the daemon's number");
    await sleep(30);
    expect(mine(v.get().chat, 'hello there')).toHaveLength(1);
  });

  it('a refused send stays as a failed row with its text and the reason; Retry sends it again, Discard drops it', async () => {
    const { store, daemon } = await connected();
    const v = view();
    await mount(store, v.node);
    await until(() => v.get().chat.status === 'ready', 'the conversation');
    daemon.knobs.refuse('chat.send', 'state', 'the agent is parked', 'hl wake');
    act(() => v.get().act.send('keep me'));
    await until(() => mine(v.get().chat, 'keep me')[0]?.state === 'failed', 'the failed row');
    const row = mine(v.get().chat, 'keep me')[0]!;
    expect(row.sending_reason).toBe('the agent is parked (hl wake)');
    expect(store.error).toBe('send: the agent is parked (hl wake)');
    daemon.knobs.clear();
    act(() => v.get().act.retry(row.id));
    await until(() => mine(v.get().chat, 'keep me').length === 1 && mine(v.get().chat, 'keep me')[0]!.id > 0, 'the retried row, numbered');
    daemon.knobs.refuse('chat.send', 'state', 'no', undefined, 1);
    act(() => v.get().act.send('drop me'));
    await until(() => mine(v.get().chat, 'drop me')[0]?.state === 'failed', 'the second failure');
    act(() => v.get().act.discard(mine(v.get().chat, 'drop me')[0]!.id));
    await until(() => mine(v.get().chat, 'drop me').length === 0, 'the discard');
  });

  it("the daemon's event for the message arrives before the reply and replaces the optimistic row, leaving one", async () => {
    const { store, daemon } = await connected();
    const v = view();
    await mount(store, v.node);
    await until(() => v.get().chat.status === 'ready', 'the conversation');
    act(() => v.get().act.send('race'));
    await until(() => mine(v.get().chat, 'race').length === 1 && mine(v.get().chat, 'race')[0]!.id > 0, 'one real row');
    daemon.settle();
    await until(() => mine(v.get().chat, 'race')[0]!.state === 'answered', 'the later events');
    expect(mine(v.get().chat, 'race')).toHaveLength(1);
  });
});
