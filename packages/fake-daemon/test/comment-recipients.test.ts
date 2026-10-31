import { test } from 'node:test';
import assert from 'node:assert/strict';
import { second, setup } from '#src/helpers.ts';

const ARCH = 'handline.architect';

// The same cases as contract/fixtures/task_comments.json (task.comment.recipients), for the fake's own rule.
test('a founder comment is told to the assignee and the agent it names, an agent comment to the agent it names only', async () => {
  const s = setup({ client: 'boss' });
  s.daemon.tasks.verbs = true; // task.comment.add is a verb of the daemon's plan
  await s.client.start();
  const owner = second(s.daemon, s.net, { client: ARCH });
  await owner.start();
  const task = (await s.client.call('task.new', { owner: ARCH, title: 'a task', body: 'build it', request_id: 'r1' })).task as number;
  const who = async (c: typeof owner, text: string) =>
    ((await c.call('task.comment.recipients', { task, text })).recipients as { agent: string; why: string }[]).map(
      (r) => `${r.agent}:${r.why}`,
    );
  assert.deepEqual(await who(s.client, `@${ARCH} look`), [`${ARCH}:asked`]);
  assert.deepEqual(await who(s.client, 'no one named'), [`${ARCH}:assignee`]);
  assert.deepEqual(await who(owner, 'no one named'), []);
  await s.client.call('task.comment.add', { task, text: `@${ARCH} look`, skip: [ARCH], request_id: 'r2' });
  assert.equal(((await s.client.call('task.comment.list', { task })).memos as unknown[]).length, 1);
  owner.close();
  s.client.close();
});
