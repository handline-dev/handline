import { QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it } from 'vitest';
import type { Event as WireEvent } from '@handline/client';
import { applyEvent, awayLine, createBridge, qk, resume, resync, writes, type Ctx } from '#src/index.ts';

const D = 'd1';
let qc: QueryClient;
let c: Ctx;
beforeEach(() => {
  qc = new QueryClient();
  c = { qc, d: D, me: 'me', now: 't' };
});

const ev = (type: string, data: object, over: Partial<WireEvent> = {}): WireEvent =>
  ({ cursor: 1, topic: 'agents', type, at: 't', actor: null, agent: null, title: null, target: null, data, ...over }) as WireEvent;
const agent = (name: string, state = 'idle') => ({ name, state, role: '', model: null, effort: null, account: null, task: null });
const stale = (key: readonly unknown[]) => qc.getQueryState(key)?.isInvalidated === true;

describe('agents', () => {
  beforeEach(() => {
    qc.setQueryData(qk.agents(D), { agents: [agent('a'), agent('b')] });
    qc.setQueryData(qk.agent(D, 'a'), { ...agent('a'), session_open: false });
  });
  it('agent.changed replaces the row in the list and patches the detail when it is cached', () => {
    applyEvent(c, ev('agent.changed', agent('a', 'working')));
    expect(qc.getQueryData<{ agents: { name: string; state: string }[] }>(qk.agents(D))!.agents.map((x) => x.state)).toEqual([
      'working',
      'idle',
    ]);
    expect(qc.getQueryData<{ state: string; session_open: boolean }>(qk.agent(D, 'a'))).toMatchObject({
      state: 'working',
      session_open: false,
    });
  });
  it('agent.changed also reads an open detail again, since the row lacks the terminal and who may type', () => {
    applyEvent(c, ev('agent.changed', agent('a', 'working')));
    expect(stale(qk.agent(D, 'a'))).toBe(true);
  });
  it('agent.changed adds an agent the list did not have, and leaves an uncached detail uncached', () => {
    applyEvent(c, ev('agent.changed', agent('c')));
    expect(qc.getQueryData<{ agents: unknown[] }>(qk.agents(D))!.agents).toHaveLength(3);
    expect(qc.getQueryData(qk.agent(D, 'c'))).toBeUndefined();
  });
  it('agent.removed leaves the list and drops the detail', () => {
    applyEvent(c, ev('agent.removed', { name: 'a' }, { agent: 'a' }));
    expect(qc.getQueryData<{ agents: { name: string }[] }>(qk.agents(D))!.agents.map((x) => x.name)).toEqual(['b']);
    expect(qc.getQueryData(qk.agent(D, 'a'))).toBeUndefined();
  });
  it("agent.control reads that agent's detail again", () => {
    applyEvent(c, ev('agent.control', {}, { agent: 'a' }));
    expect(stale(qk.agent(D, 'a'))).toBe(true);
  });
});

describe('tasks and inbox', () => {
  const task = (id: number, state = 'queued') => ({ id, title: `t${id}`, state });
  const ids = (key: readonly unknown[]) => qc.getQueryData<{ items: { id: number }[] }>(key as never)!.items.map((t) => t.id);
  it('task.changed patches the cached task and each list by its own filter, reading no list again', () => {
    qc.setQueryData(qk.task(D, 7), { ...task(7), body: 'b' });
    const open = qk.tasks(D);
    const all = [...qk.tasks(D), { all: true }];
    const doing = [...qk.tasks(D), { state: 'doing' }];
    qc.setQueryData(open, { items: [task(9), task(7)], more: false });
    qc.setQueryData(all, { items: [task(9), task(7), task(3, 'done')], more: false });
    qc.setQueryData(doing, { items: [], more: false });
    applyEvent(c, ev('task.changed', task(7, 'doing'), { topic: 'tasks' }));
    expect(qc.getQueryData(qk.task(D, 7))).toMatchObject({ state: 'doing', body: 'b' });
    expect(ids(doing)).toEqual([7]);
    expect(qc.getQueryData<{ items: { state: string }[] }>(open)!.items[1]!.state).toBe('doing');
    applyEvent(c, ev('task.changed', task(7, 'done'), { topic: 'tasks' }));
    expect(ids(open)).toEqual([9]); // closed: out of the open list
    expect(ids(doing)).toEqual([]);
    expect(ids(all)).toEqual([9, 7, 3]);
    applyEvent(c, ev('task.changed', task(12), { topic: 'tasks' }));
    expect(ids(open)).toEqual([12, 9]);
    for (const k of [open, all, doing]) expect(stale(k)).toBe(false);
  });
  it("task.changed reads that task's detail again, for a body the row does not carry, and only that task's", () => {
    qc.setQueryData(qk.task(D, 7), { ...task(7), body: 'old' });
    qc.setQueryData(qk.task(D, 8), { ...task(8), body: 'b' });
    applyEvent(c, ev('task.changed', task(7), { topic: 'tasks' }));
    expect(stale(qk.task(D, 7))).toBe(true);
    expect(stale(qk.task(D, 8))).toBe(false);
  });
  it("task.changed reads that task's history again, and only that task's", () => {
    qc.setQueryData(qk.taskHistory(D, 7), { task: 7, rows: [] });
    qc.setQueryData(qk.taskHistory(D, 8), { task: 8, rows: [] });
    applyEvent(c, ev('task.changed', task(7, 'doing'), { topic: 'tasks' }));
    expect(stale(qk.taskHistory(D, 7))).toBe(true);
    expect(stale(qk.taskHistory(D, 8))).toBe(false);
  });
  it("a task or memo write reads the task's history again once it settles", () => {
    const touched = (method: string, p: object) => writes[method]!.touches!(D, p as never).map((k) => JSON.stringify(k));
    const history = JSON.stringify(qk.taskHistory(D, 7));
    for (const [method, p] of [
      ['task.edit', { task: 7, title: 'x' }],
      ['task.done', { task: 7 }],
      ['task.queue', { tasks: [7] }],
      ['task.comment.add', { task: 7, text: 'x' }],
      ['task.comment.edit', { task: 7, memo: 1, text: 'x' }],
      ['task.comment.delete', { task: 7, memo: 1 }],
    ] as const)
      expect(touched(method, p), method).toContain(history);
  });
  it('task.changed leaves a full page alone for a task that belongs on the next one, and reads a list it cannot judge', () => {
    const page = [...qk.tasks(D), { limit: 2 }];
    const under = [...qk.tasks(D), { under: 4 }];
    qc.setQueryData(page, { items: [task(9), task(7)], more: true });
    qc.setQueryData(under, { items: [task(5)], more: false });
    applyEvent(c, ev('task.changed', task(2), { topic: 'tasks' }));
    expect(ids(page)).toEqual([9, 7]);
    expect(stale(under)).toBe(true);
  });
  const item = (id: number, read = false) => ({ id, kind: 'result', title: `i${id}`, read });
  it('inbox.added puts the item first in the unfiltered list and reads a filtered list again', () => {
    qc.setQueryData(qk.inbox(D), { items: [item(1)], more: false });
    qc.setQueryData(qk.inbox(D, { unread: true }), { items: [], more: false });
    applyEvent(c, ev('inbox.added', item(2), { topic: 'inbox' }));
    expect(qc.getQueryData<{ items: { id: number }[] }>(qk.inbox(D))!.items.map((i) => i.id)).toEqual([2, 1]);
    expect(stale(qk.inbox(D, { unread: true }))).toBe(true);
    expect(stale(qk.inbox(D))).toBe(false);
  });
  it('an inbox event reads the agent list again, for founder_unread', () => {
    qc.setQueryData(qk.agents(D), { agents: [] });
    applyEvent(c, ev('inbox.changed', item(1), { topic: 'inbox' }));
    expect(stale(qk.agents(D))).toBe(true);
  });
  it('inbox.changed replaces the item in place', () => {
    qc.setQueryData(qk.inbox(D), { items: [item(1), item(2)], more: false });
    applyEvent(c, ev('inbox.changed', item(1, true), { topic: 'inbox' }));
    expect(qc.getQueryData<{ items: { id: number; read: boolean }[] }>(qk.inbox(D))!.items.map((i) => [i.id, i.read])).toEqual([
      [1, true],
      [2, false],
    ]);
  });
});

describe('accounts, status, operations, devices, prompts', () => {
  it('account.changed replaces the account', () => {
    qc.setQueryData(qk.accounts(D), { accounts: [{ name: 'x', label: 'old' }] });
    applyEvent(c, ev('account.changed', { name: 'x', label: 'new' }, { topic: 'accounts' }));
    expect(qc.getQueryData(qk.accounts(D))).toEqual({ accounts: [{ name: 'x', label: 'new' }] });
  });
  it('status.changed sets the status', () => {
    applyEvent(c, ev('status.changed', { alive: true }, { topic: 'status' }));
    expect(qc.getQueryData(qk.status(D))).toEqual({ alive: true });
  });
  it('operation.progress sets that operation', () => {
    applyEvent(c, ev('operation.progress', { id: 'op1', state: 'running' }, { topic: 'operations' }));
    expect(qc.getQueryData(qk.operation(D, 'op1'))).toEqual({ id: 'op1', state: 'running' });
  });
  it("prompt_asked reads the agent's prompts and detail again", () => {
    qc.setQueryData(qk.prompts(D, 'a'), { prompts: [] });
    qc.setQueryData(qk.agent(D, 'a'), agent('a'));
    applyEvent(c, ev('prompt_asked', { agent: 'a' }, { topic: 'notifications', agent: 'a' }));
    expect(stale(qk.prompts(D, 'a'))).toBe(true);
    expect(stale(qk.agent(D, 'a'))).toBe(true);
  });
  it('a pairing or a revoke reads the device list again', () => {
    qc.setQueryData(['d', D, 'devices'], { devices: [] });
    applyEvent(c, ev('device.paired', {}, { topic: 'devices' }));
    expect(stale(['d', D, 'devices'])).toBe(true);
  });
  it('task_done, stall and account_out change no cache (the shell toasts them)', () => {
    qc.setQueryData(qk.agents(D), { agents: [] });
    for (const t of ['task_done', 'stall', 'account_out']) applyEvent(c, ev(t, {}, { topic: 'notifications' }));
    expect(qc.getQueryData(qk.agents(D))).toEqual({ agents: [] });
    expect(stale(qk.agents(D))).toBe(false);
  });
});

describe('gap and reconnect', () => {
  it("mark every query of this daemon stale and no other daemon's (mounted ones read again: hooks.test)", async () => {
    const key = [...qk.agents(D)];
    const obs = qc.getQueryCache().build(qc, { queryKey: key, queryFn: () => ({ agents: [] }) });
    await obs.fetch();
    qc.setQueryData(['d', 'other', 'agents'], { agents: [] });
    resync(qc, D);
    expect(stale(key)).toBe(true);
    expect(qc.getQueryState(['d', 'other', 'agents'])?.isInvalidated).toBe(false);
  });
});

describe('an event the read already holds', () => {
  it('is left out of an entry read at or past its cursor, and applied to one read before it', () => {
    qc.setQueryData(qk.agents(D), { agents: [agent('a')], cursor: 10 });
    applyEvent(c, ev('agent.changed', agent('a', 'working'), { cursor: 10 }));
    expect(qc.getQueryData<{ agents: { state: string }[] }>(qk.agents(D))!.agents[0]!.state).toBe('idle');
    applyEvent(c, ev('agent.changed', agent('a', 'working'), { cursor: 11 }));
    expect(qc.getQueryData<{ agents: { state: string }[] }>(qk.agents(D))!.agents[0]!.state).toBe('working');
  });
  it('leaves a status the read already answered', () => {
    qc.setQueryData(qk.status(D), { alive: true, cursor: 20 });
    applyEvent(c, ev('status.changed', { alive: false }, { topic: 'status', cursor: 19 }));
    expect(qc.getQueryData<{ alive: boolean }>(qk.status(D))!.alive).toBe(true);
  });
});

describe('resume', () => {
  const pages = (n: number) => ({
    pages: Array.from({ length: n }, () => ({ messages: [], more: true })),
    pageParams: Array.from({ length: n }, (_, i) => i || undefined),
  });
  const key = (agent: string) => qk.chatMessages(D, agent, 'mine');
  it('drops a chat to its first page unless the person is reading up in it, and reads every query again', () => {
    qc.setQueryData(key('a'), pages(3));
    qc.setQueryData(key('b'), pages(3));
    resume(qc, D, new Set(['b']));
    expect(qc.getQueryData<{ pages: unknown[]; pageParams: unknown[] }>(key('a'))!.pages).toHaveLength(1);
    expect(qc.getQueryData<{ pageParams: unknown[] }>(key('a'))!.pageParams).toHaveLength(1);
    expect(qc.getQueryData<{ pages: unknown[] }>(key('b'))!.pages).toHaveLength(3);
    expect(stale(key('a'))).toBe(true);
  });
});

describe('the line for what was missed', () => {
  it('counts, and is a floor when the feed was cut', () => {
    expect(awayLine({ count: 1, more: false })).toBe('1 notice while you were away');
    expect(awayLine({ count: 3, more: false })).toBe('3 notices while you were away');
    expect(awayLine({ count: 100, more: true })).toBe('100+ notices while you were away');
  });
});

describe('the bridge', () => {
  it('applies events together in a microtask, in the order they came', async () => {
    qc.setQueryData(qk.agents(D), { agents: [agent('a')] });
    const bridge = createBridge(qc, D, () => 'me');
    bridge.push(ev('agent.changed', agent('a', 'one')));
    bridge.push(ev('agent.changed', agent('a', 'two')));
    expect(qc.getQueryData<{ agents: { state: string }[] }>(qk.agents(D))!.agents[0]!.state).toBe('idle');
    await Promise.resolve();
    expect(qc.getQueryData<{ agents: { state: string }[] }>(qk.agents(D))!.agents[0]!.state).toBe('two');
  });
});
