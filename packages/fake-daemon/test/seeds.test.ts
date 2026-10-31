import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FakeDaemon } from '#src/daemon.ts';
import { applySeeds, SEEDS } from '#src/seeds.ts';
import { setup } from '#src/helpers.ts';

test('an unknown seed is refused with the known names', () => {
  assert.throws(() => applySeeds(new FakeDaemon(), 'nope'), /known: inbox-terminal/);
});

test('inbox-terminal and hr come through the ordinary methods', async () => {
  const { client, daemon } = setup();
  applySeeds(daemon, 'inbox-terminal,hr');
  await client.start();
  const inbox = await client.call('inbox.list', {});
  assert.ok(inbox.items.some((i) => i.fix === 'open_terminal' && i.agent));
  const { proposals } = await client.call('hr.list', {});
  assert.deepEqual(
    proposals.map((p) => p.kind),
    ['add', 'settings', 'retire'],
  );
  assert.equal(proposals[0]!.asked?.team, 'handline');
  assert.equal(proposals[1]!.asked?.sets?.model, 'opus');
  client.close();
});

test('chat-actions puts the OK and No buttons on a message', async () => {
  const { client, daemon } = setup();
  applySeeds(daemon, 'chat-actions');
  await client.start();
  const { messages } = await client.call('chat.messages', { agent: 'handline.architect', scope: 'all' });
  const keys = messages
    .flatMap((m) => m.actions ?? [])
    .map((a) => a.key)
    .sort();
  assert.deepEqual(keys, ['no', 'ok']);
  client.close();
});

test('tasks-200 seeds 200 tasks, and all switches on every seed', () => {
  const d = new FakeDaemon();
  d.tasks.verbs = true;
  assert.deepEqual(applySeeds(d, 'all'), Object.keys(SEEDS));
  assert.equal(d.tasks.find(6000).id, 6000);
  assert.equal(d.tasks.find(6199).id, 6199);
});

test('review: a task tagged review whose first comment names two pictures the daemon serves', () => {
  const daemon = new FakeDaemon();
  applySeeds(daemon, 'review');
  const { memos } = daemon.tasks.comments({ task: 9301 });
  const images = memos[0]!.parts!.flatMap((p) => ('image' in p ? [p.image.id] : []));
  assert.equal(images.length, 2);
  assert.equal(daemon.uploads.get({ id: images[0] }).mime, 'image/png');
});

test('my-requests: the founder has five open requests, one waiting on them and one blocked', () => {
  const d = new FakeDaemon();
  applySeeds(d, 'my-requests');
  const items = d.requests.rows;
  assert.equal(items.filter((r) => r.kind === 'task').length, 5);
  assert.equal(items.filter((r) => r.status === 'waiting_on_you').length, 1);
  assert.equal(items.filter((r) => r.blocked === true).length, 1);
});
