import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type Event, type RpcError } from '@handline/client';
import { setup, until } from '../src/helpers.ts';

const make = (name: string, more: Record<string, unknown> = {}) => ({
  name,
  role: 'worker',
  repo: '/work/handline',
  base: 'main',
  request_id: `req-${name}`,
  ...more,
});

test('agent.create replies with an operation that runs to done, and the agent appears running', async () => {
  const { client, daemon } = setup();
  await client.start();
  const seen: Event[] = [];
  client.onEvent((e) => e.topic === 'operations' && seen.push(e));
  await client.subscribe(['operations', 'agents']);
  const r = await client.call('agent.create', make('conf.made'));
  assert.equal(r.accepted, true);
  await until(() => seen.some((e) => (e.data as { state: string }).state === 'done'), 'done');
  const steps = seen.map((e) => (e.data as { step: string | null }).step);
  assert.deepEqual(steps, ['registering the agent', 'making its worktree', 'starting the agent', null]);
  const op = await client.call('operation.get', { operation: r.operation });
  assert.deepEqual([op.state, op.agent, op.result], ['done', 'conf.made', { agent: 'conf.made', registered: true }]);
  assert.equal(daemon.sessions.get('conf.made')?.state, 'idle');
  assert.equal(daemon.admin.worktrees.rows.some((w) => w.owner === 'conf.made' && w.repo === 'handline'), true);
  client.close();
});

test('a base the repo does not have fails the operation and leaves the agent registered', async () => {
  const { client, daemon } = setup();
  await client.start();
  const seen: Event[] = [];
  client.onEvent((e) => e.topic === 'operations' && seen.push(e));
  await client.subscribe(['operations']);
  const r = await client.call('agent.create', make('conf.bad', { base: 'nope' }));
  await until(() => seen.some((e) => (e.data as { state: string }).state === 'failed'), 'failed');
  const op = await client.call('operation.get', { operation: r.operation });
  assert.deepEqual([op.state, op.message, op.result], ['failed', 'no branch nope in handline', { agent: 'conf.bad', registered: true }]);
  assert.equal(daemon.sessions.get('conf.bad')?.state, 'parked');
  client.close();
});

test('agent.create checks its params in order and starts nothing when one is wrong', async () => {
  const { client, daemon } = setup();
  await client.start();
  const bad = async (p: Record<string, unknown>, code: string, key: 'field' | 'what' | 'gate', value: string) =>
    await assert.rejects(client.call('agent.create', p as never), (e: RpcError) => e.code === code && e.data?.[key] === value);
  await bad(make('x/y'), 'bad_request', 'field', 'name');
  await bad(make('ok.one', { effort: 'extreme', repo: '/nope' }), 'bad_request', 'field', 'effort');
  await bad(make('ok.one', { account: 'alpha', repo: '/nope' }), 'bad_request', 'field', 'account');
  await bad(make('ok.one', { repo: '/nope' }), 'not_found', 'what', 'repo');
  await bad(make('ok.one', { parent: 'no.one' }), 'not_found', 'what', 'agent');
  await bad(make('other', { parent: 'handline.architect' }), 'bad_request', 'field', 'name');
  await bad(make('handline.auditor'), 'refused', 'gate', 'state');
  assert.equal(daemon.sessions.has('ok.one'), false);
  client.close();
});

test('only a founder creates an agent; a daemon that does not list it refuses it', async () => {
  const member = setup({ role: 'member' });
  await member.client.start();
  await assert.rejects(member.client.call('agent.create', make('conf.m')), (e: RpcError) => e.gate === 'role');
  member.client.close();
  const { client, daemon } = setup();
  daemon.knobs.unserve('agent.create');
  await client.start();
  assert.equal(client.hello?.methods.some((m) => m.name === 'agent.create'), false);
  client.close();
});

test('worktree.set_status sets a label that worktrees.list shows, and refuses a path that is no worktree', async () => {
  const { client, daemon } = setup({ role: 'member' });
  await client.start();
  const path = '/work/handline.auditor';
  assert.equal(daemon.admin.worktrees.rows.find((w) => w.path === path)?.status, 'todo');
  await client.call('worktree.set_status', { path, status: 'in review', request_id: 'ws-1' });
  const { worktrees } = await client.call('worktrees.list', { owner: 'handline.auditor' });
  assert.deepEqual(worktrees.map((w) => [w.path, w.status, w.parent]), [[path, 'in review', '/work/handline.architect']]);
  assert.equal(daemon.admin.worktrees.rows.find((w) => w.path === path)?.status, 'in review');
  await assert.rejects(client.call('worktree.set_status', { path: '/', status: 'done', request_id: 'ws-2' }), (e: RpcError) => e.data?.what === 'worktree');
  await assert.rejects(client.call('worktree.set_status', { path, status: 'x'.repeat(41), request_id: 'ws-3' }), (e: RpcError) => e.data?.field === 'status');
  
  client.close();
});
