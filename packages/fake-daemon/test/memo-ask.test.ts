import { test } from 'node:test';
import assert from 'node:assert/strict';
import { second, setup } from '#src/helpers.ts';

const AGENT = 'handline.architect';

test('an agent comment naming @human opens an ask; edits, the reply and done close it', async () => {
  const s = setup({ client: 'boss' });
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const worker = second(s.daemon, s.net, { client: AGENT });
  await worker.start();
  const task = (await s.client.call('task.new', { owner: AGENT, title: 'a task', body: 'build it', request_id: 'a1' })).task as number;
  const asks = async () => ((await s.client.call('inbox.list', { unread: true })).items as { kind: string; task: number; memo?: number }[]).filter((i) => i.memo !== undefined);
  const none = (await worker.call('task.comment.add', { task, text: 'no one named', request_id: 'a2' })).memo as number;
  assert.deepEqual(await asks(), []);
  const memo = (await worker.call('task.comment.add', { task, text: '@human which one?', request_id: 'a3' })).memo as number;
  const [ask] = await asks();
  assert.deepEqual([ask?.kind, ask?.task, ask?.memo], ['ask', task, memo]);
  await worker.call('task.comment.edit', { task, memo, text: '@human A or B?', request_id: 'a4' });
  assert.equal((await asks()).length, 1);
  await worker.call('task.comment.edit', { task, memo, text: 'never mind', request_id: 'a5' });
  assert.deepEqual(await asks(), []);
  await worker.call('task.comment.edit', { task, memo: none, text: '@human now', request_id: 'a6' });
  assert.equal((await asks()).length, 1);
  const reply = (await asks())[0] as unknown as { id: number };
  await s.client.call('inbox.reply', { id: reply.id, text: 'A', request_id: 'a7' });
  assert.deepEqual(await asks(), []);
  const comments = (await s.client.call('task.comment.list', { task })).memos as { text: string; reply_to?: number }[];
  assert.ok(comments.some((m) => m.text === 'A' && m.reply_to === none));
  await worker.call('task.comment.add', { task, text: '@human again', request_id: 'a8' });
  assert.equal((await asks()).length, 1);
  await worker.call('task.done', { task, evidence: 'built and tested', request_id: 'a9' });
  assert.deepEqual(await asks(), []);
  worker.close();
  s.client.close();
});
