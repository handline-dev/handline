import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { second, setup } from '../src/helpers.ts';

const ARCH = 'handline.architect';
const code = (e: unknown) => (e as RpcError).data?.code;

async function reported() {
  const s = setup({ client: 'boss' });
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const owner = second(s.daemon, s.net, { client: ARCH });
  await owner.start();
  const made = await s.client.call('task.new', { owner: ARCH, title: 'a reported task', body: 'build it', request_id: 'a1' });
  const task = made.task as number;
  const said = await owner.call('task.done', { task, evidence: 'built and tested', request_id: 'a2' });
  return { ...s, owner, task, said };
}

test('a done by an owner the task does not answer to leaves it reported, with the report', async () => {
  const { client, owner, task, said, daemon } = await reported();
  assert.match(String(said.said), /reported done/);
  const t = await client.call('task.get', { task });
  assert.deepEqual([t.state, t.evidence, t.report?.by, t.report?.evidence], ['reported', null, ARCH, 'built and tested']);
  const again = await owner.call('task.done', { task, evidence: 'again', request_id: 'a3' }).catch((e) => e);
  assert.equal(code(again), 'refused');
  owner.close();
  client.close();
  void daemon;
});

test('accept closes a report with its evidence, and a second accept is refused', async () => {
  const { client, owner, task } = await reported();
  assert.equal((await owner.call('task.accept', { task, request_id: 'b1' }).catch((e) => e)).data?.code, 'refused'); // not the owner's to accept
  await client.call('task.accept', { task, request_id: 'b2' });
  const t = await client.call('task.get', { task });
  assert.deepEqual([t.state, t.evidence, t.report], ['done', 'built and tested', null]);
  assert.equal(code(await client.call('task.accept', { task, request_id: 'b3' }).catch((e) => e)), 'refused');
  owner.close();
  client.close();
});

test('reject needs a note and sends the task back to its owner, report dropped', async () => {
  const { client, owner, task } = await reported();
  const bare = await client.call('task.reject', { task, note: ' ', request_id: 'c1' }).catch((e) => e);
  assert.equal(code(bare), 'bad_request');
  assert.equal((bare as RpcError).data?.field, 'note');
  await client.call('task.reject', { task, note: 'the tests are missing', request_id: 'c2' });
  const t = await client.call('task.get', { task });
  assert.deepEqual([t.state, t.to, t.report], ['queued', ARCH, null]);
  assert.equal(code(await client.call('task.reject', { task, note: 'again', request_id: 'c3' }).catch((e) => e)), 'refused');
  owner.close();
  client.close();
});

test('an unknown task is not found, and a task the owner opened for itself closes on its done', async () => {
  const { client, owner } = await reported();
  assert.equal(code(await client.call('task.accept', { task: 999999, request_id: 'd1' }).catch((e) => e)), 'not_found');
  const mine = (await owner.call('task.new', { owner: ARCH, title: 'my own', request_id: 'd2' })).task as number;
  await owner.call('task.done', { task: mine, evidence: 'done', request_id: 'd3' });
  assert.equal((await owner.call('task.get', { task: mine })).state, 'done');
  assert.equal(code(await owner.call('task.accept', { task: mine, request_id: 'd4' }).catch((e) => e)), 'refused');
  owner.close();
  client.close();
});

test('a task can be seeded as reported', async () => {
  const { client, daemon } = setup();
  daemon.tasks.verbs = true;
  await client.start();
  daemon.tasks.seed({
    id: 7001,
    from: 'boss',
    to: ARCH,
    title: 'seeded',
    state: 'reported',
    report: { by: ARCH, evidence: 'seen', at: new Date().toISOString() },
  });
  await client.call('task.accept', { task: 7001, request_id: 'e1' });
  assert.equal((await client.call('task.get', { task: 7001 })).state, 'done');
  client.close();
});
