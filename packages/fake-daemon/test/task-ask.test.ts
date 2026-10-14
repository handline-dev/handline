import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

test('task.ask sends the question to the agent, linked to the task, and the reply joins it', async () => {
  const { client } = setup();
  await client.start();
  const task = (await client.call('tasks.list', {})).items[0]!;
  const said = await client.call('task.ask', { task: task.id, to: 'handline.architect', text: ' why this? ', request_id: 'ask-1' });
  assert.equal(said.accepted, true);
  const page = await client.call('chat.messages', { agent: 'handline.architect', scope: 'all', limit: 5 });
  const q = page.messages.find((m) => m.id === (said as unknown as { request: number }).request)!;
  assert.deepEqual(q.tasks, [{ id: task.id, title: task.title }]);
  assert.match(q.body, /^Question on T\d+ from the founder \(the task keeps its holder\): why this\?/);
});

test('task.ask refuses what the daemon refuses', async () => {
  const { client } = setup();
  await client.start();
  const task = (await client.call('tasks.list', {})).items[0]!;
  const ask = (p: Record<string, unknown>) => client.call('task.ask', { request_id: 'r', ...p } as never);
  await assert.rejects(ask({ task: task.id, to: 'handline.architect', text: '  ' }), (e: RpcError) => e.data?.field === 'text');
  await assert.rejects(ask({ task: 999999999, to: 'handline.architect', text: 'why?' }), (e: RpcError) => e.data?.code === 'not_found');
  await assert.rejects(ask({ task: task.id, to: 'no.such.agent', text: 'why?' }), (e: RpcError) => e.data?.code === 'not_found');
});

test('chat.refs lists the agent newest requests, one line each', async () => {
  const { client } = setup();
  await client.start();
  const sent = await client.call('chat.send', { agent: 'handline.architect', body: 'first line\nsecond', request_id: 'refs-1' });
  const { requests } = await client.call('chat.refs', { agent: 'handline.architect' });
  assert.equal(requests[0]!.request, sent.request);
  assert.equal(requests[0]!.line, 'first line');
  assert.ok(requests.length <= 30);
  await assert.rejects(client.call('chat.refs', { agent: 'no.such.agent' }), (e: RpcError) => e.data?.code === 'not_found');
});
