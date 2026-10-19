import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

test('task.time covers a new task from its opening, and names a bad or unknown task', async () => {
  const s = setup({ client: 'boss' });
  await s.client.start();
  const made = await s.client.call('task.new', { owner: 'handline.architect', title: 'a timed task', body: 'time it', request_id: 't1' });
  const got = await s.client.call('task.time', { task: made.task as number });
  assert.equal(got.end, null);
  for (const seg of got.segments) assert.equal(seg.seconds, Math.round((Date.parse(seg.to) - Date.parse(seg.from)) / 1000));
  const bad = (await s.client.call('task.time', { task: 'T1' as never }).catch((e) => e)) as RpcError;
  assert.deepEqual([bad.data?.code, bad.data?.field], ['bad_request', 'task']);
  const none = (await s.client.call('task.time', { task: 999999 }).catch((e) => e)) as RpcError;
  assert.equal(none.data?.code, 'not_found');
  s.client.close();
});

test('task.time carries the estimate task.new and task.edit set, and an open task expects a start', async () => {
  const s = setup({ client: 'boss' });
  await s.client.start();
  const made = await s.client.call('task.new', { owner: 'handline.architect', title: 'sized', estimate: 3, request_id: 'e1' });
  const task = made.task as number;
  let got = await s.client.call('task.time', { task });
  assert.equal(got.estimate, 3);
  assert.ok(got.expected?.start);
  await s.client.call('task.edit', { task, estimate: null, request_id: 'e2' });
  got = await s.client.call('task.time', { task });
  assert.equal(got.estimate, null);
  s.client.close();
});
