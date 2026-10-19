import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { second, setup } from '../src/helpers.ts';

const ARCH = 'handline.architect';
const code = (e: unknown) => (e as RpcError).data?.code;

test('a done by the owner closes the task with its evidence, and a second done is refused', async () => {
  const s = setup({ client: 'boss' });
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const owner = second(s.daemon, s.net, { client: ARCH });
  await owner.start();
  const task = (await s.client.call('task.new', { owner: ARCH, title: 'a task', body: 'build it', request_id: 'a1' })).task as number;
  await owner.call('task.done', { task, evidence: 'built and tested', request_id: 'a2' });
  const t = await s.client.call('task.get', { task });
  assert.deepEqual([t.state, t.evidence], ['done', 'built and tested']);
  assert.equal(code(await owner.call('task.done', { task, evidence: 'again', request_id: 'a3' }).catch((e) => e)), 'refused');
  owner.close();
  s.client.close();
});
