import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

const code = (e: unknown) => (e as RpcError).data?.code;

async function twoTasks() {
  const s = setup({ client: 'boss' });
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const one = (await s.client.call('task.new', { owner: 'handline.architect', title: 'one', body: 'b', request_id: 'n1' })).task as number;
  const two = (await s.client.call('task.new', { owner: 'handline.architect', title: 'two', body: 'b', request_id: 'n2' })).task as number;
  return { ...s, one, two };
}

test('a batch discard closes every task under its own reason, or none', async () => {
  const { client, one, two } = await twoTasks();
  const short = await client.call('task.discard', { tasks: [one, two], reasons: { [one]: 'moot' }, request_id: 'd1' }).catch((e) => e);
  assert.equal(code(short), 'bad_request'); // two has no reason
  assert.equal((await client.call('task.get', { task: one })).state, 'queued');
  await client.call('task.discard', { tasks: [one, two], reason: 'done elsewhere', reasons: { [`T${two}`]: 'merged' }, request_id: 'd2' });
  for (const task of [one, two]) assert.equal((await client.call('task.get', { task })).state, 'discarded');
  client.close();
});

test('a discarded task is reopened as a draft', async () => {
  const { client, one } = await twoTasks();
  await client.call('task.discard', { task: one, reason: 'x', request_id: 'd3' });
  await client.call('task.reopen', { task: one, request_id: 'r1' });
  assert.equal((await client.call('task.get', { task: one })).state, 'draft');
  client.close();
});
