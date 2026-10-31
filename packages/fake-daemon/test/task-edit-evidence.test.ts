import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { second, setup } from '#src/helpers.ts';

const code = (e: unknown) => (e as RpcError).data?.code;

test('a done task keeps its brief and result editable, and an open one has no result to edit', async () => {
  const s = setup({ client: 'boss' });
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const task = (await s.client.call('task.new', { owner: 'handline.architect', title: 'a task', body: 'build it', request_id: 'e1' }))
    .task as number;
  assert.equal(code(await s.client.call('task.edit', { task, evidence: 'early', request_id: 'e2' }).catch((e) => e)), 'refused');
  await s.client.call('task.done', { task, evidence: 'built', request_id: 'e3' });
  await s.client.call('task.edit', { task, body: 'new brief', evidence: 'rebuilt', request_id: 'e4' });
  const t = await s.client.call('task.get', { task });
  assert.deepEqual([t.state, t.body, t.evidence], ['done', 'new brief', 'rebuilt']);
  assert.equal(code(await s.client.call('task.edit', { task, evidence: ' ', request_id: 'e5' }).catch((e) => e)), 'refused');
  const h = await s.client.call('task.history', { task });
  assert.ok(h.rows.some((r) => r.field === 'evidence' && r.was === 'built' && r.now === 'rebuilt'));
  const agent = second(s.daemon, s.net, { role: 'agent', client: 'handline.architect' });
  await agent.start();
  assert.equal(code(await agent.call('task.edit', { task, evidence: 'PASS', request_id: 'e6' }).catch((e) => e)), 'refused');
  agent.close();
  s.client.close();
});
