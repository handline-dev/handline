// Images named in a task: the reference form, the checks, and what task.get reads back (docs/story1-task-images-graph.md section A).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from '../src/helpers.ts';

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const upload = async (client: ReturnType<typeof setup>['client']) =>
  (
    await client.call('attachment.put', {
      name: 'shot.png',
      mime: 'image/png',
      bytes: PNG.length,
      data: PNG.toString('base64'),
      request_id: `op-${Math.random()}`,
    } as never)
  ).attachment!;

test('a task body names an image: task.get gives the parts, and an unknown id is refused', async () => {
  const s = setup();
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const img = await upload(s.client);
  const body = `Cut off:\n![the button](attachment:${img.id})\nWrap it.`;
  const made = (await s.client.call('task.new', { owner: 'handline.auditor', title: 'Fix', body, request_id: 'op-n' } as never)) as { task: number };
  const t = (await s.client.call('task.get', { task: made.task } as never)) as { body: string; body_parts?: unknown[] };
  assert.equal(t.body, body);
  assert.deepEqual(t.body_parts, [{ text: 'Cut off:\n' }, { image: img, caption: 'the button' }, { text: '\nWrap it.' }]);
  await assert.rejects(
    s.client.call('task.new', { owner: 'handline.auditor', title: 'x', body: '![a](attachment:nope)', request_id: 'op-m' } as never),
    /not an uploaded image/,
  );
  s.client.close();
});

test('a memo with an image has parts, and an image that is let go of reads as gone', async () => {
  const s = setup();
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const img = await upload(s.client);
  const made = (await s.client.call('task.new', { owner: 'handline.auditor', title: 'Fix', request_id: 'op-n' } as never)) as { task: number };
  await s.client.call('task.memo.add', { task: made.task, text: `see ![shot](attachment:${img.id})`, request_id: 'op-a' } as never);
  s.daemon.uploads.expire(img.id);
  const t = (await s.client.call('task.get', { task: made.task } as never)) as { memos: { parts?: unknown[] }[]; body_parts?: unknown };
  assert.equal(t.body_parts, undefined);
  assert.deepEqual(t.memos[0]!.parts, [{ text: 'see ' }, { image: { id: img.id }, caption: 'shot', gone: true }]);
  s.client.close();
});
