import { QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it } from 'vitest';
import type { Event as WireEvent } from '@handline/client';
import { applyEvent, createBridge, qk, resync, type Ctx } from '../src/index.ts';

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
  it('task.changed patches the cached task and reads the lists again, since a filter makes a patch unsafe', () => {
    qc.setQueryData(qk.task(D, 7), { ...task(7), body: 'b' });
    qc.setQueryData([...qk.tasks(D), { state: 'doing' }], { items: [task(7)], more: false });
    applyEvent(c, ev('task.changed', task(7, 'doing'), { topic: 'tasks' }));
    expect(qc.getQueryData(qk.task(D, 7))).toMatchObject({ state: 'doing', body: 'b' });
    expect(stale([...qk.tasks(D), { state: 'doing' }])).toBe(true);
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
