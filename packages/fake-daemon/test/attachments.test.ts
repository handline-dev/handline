import { test } from 'node:test';
import assert from 'node:assert/strict';
import { second, setup } from '#src/helpers.ts';

const AUD = 'handline.auditor';
// A 1x1 PNG (70 bytes), a PNG header padded out to a chosen size, and bytes that are not an image.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const png = (n: number) => Buffer.concat([PNG.subarray(0, 8), Buffer.alloc(n - 8, 1)]);
const put = (client: ReturnType<typeof setup>['client'], b: Buffer, p: Record<string, unknown> = {}) =>
  client.call('attachment.put', {
    name: 'a.png',
    mime: 'image/png',
    bytes: b.length,
    data: b.toString('base64'),
    request_id: `op-${Math.random()}`,
    ...p,
  } as never);

test('a small image is one chunk, comes back whole, and is sent with a message', async () => {
  const { client } = setup();
  await client.start();
  const r = await put(client, PNG);
  assert.equal(r.received, PNG.length);
  assert.equal(r.attachment?.name, 'a.png');
  const got = await client.call('attachment.get', { id: r.attachment!.id });
  assert.equal(got.done, true);
  assert.deepEqual(Buffer.from(got.data, 'base64'), PNG);
  const sent = await client.call('chat.send', { agent: AUD, body: '', attachments: [r.attachment!.id], request_id: 'op-s' });
  const m = (await client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages.find((x) => x.id === sent.request)!;
  assert.deepEqual(m.attachments, [r.attachment]);
  await client.call('chat.send', { agent: AUD, body: 'again', attachments: [r.attachment!.id], request_id: 'op-t' });
  client.close();
});

test("a message's images are its body's references, then the listed ids it does not name, cut into parts", async () => {
  const { client } = setup();
  await client.start();
  const [a, b] = [(await put(client, PNG)).attachment!, (await put(client, png(100))).attachment!];
  const body = `see ![the button](attachment:${a.id})`;
  const sent = await client.call('chat.send', { agent: AUD, body, attachments: [b.id, a.id], request_id: 'op-m' });
  const m = (await client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages.find((x) => x.id === sent.request)!;
  assert.deepEqual(m.attachments, [a, b]);
  assert.deepEqual(m.parts, [{ text: 'see ' }, { image: a, caption: 'the button' }, { image: b, caption: '' }]);
  const refs = ['1', '2', '3', '4'].map((n) => `![x](attachment:000000000000000${n})`).join('');
  await assert.rejects(
    client.call('chat.send', { agent: AUD, body: refs, attachments: ['0000000000000005', '0000000000000006', '0000000000000007'], request_id: 'op-n' } as never),
    (e: { data?: { field?: string } }) => e.data?.field === 'attachments',
  );
  await assert.rejects(
    client.call('chat.send', { agent: AUD, body: '![x](attachment:0000000000000000)', request_id: 'op-o' }),
    (e: { data?: { what?: string } }) => e.data?.what === 'attachment',
  );
  client.close();
});

test('a big image goes in chunks; a chunk at the wrong offset says what the daemon holds; a repeated chunk id writes nothing', async () => {
  const { client } = setup();
  await client.start();
  const big = png(600 * 1024);
  const first = await put(client, big.subarray(0, 512 * 1024), { bytes: big.length, request_id: 'c1' });
  assert.equal(first.received, 512 * 1024);
  assert.equal(first.attachment, undefined);
  const again = await put(client, big.subarray(0, 512 * 1024), { bytes: big.length, request_id: 'c1' });
  assert.equal((again as { replayed?: boolean }).replayed, true);
  await assert.rejects(
    put(client, big.subarray(0, 10), { bytes: big.length, upload: first.upload, offset: 5, request_id: 'c2' }),
    (e: { data?: { field?: string; received?: number } }) => e.data?.field === 'offset' && e.data?.received === 512 * 1024,
  );
  const last = await put(client, big.subarray(512 * 1024), {
    bytes: big.length,
    upload: first.upload,
    offset: 512 * 1024,
    request_id: 'c3',
  });
  assert.equal(last.attachment?.bytes, big.length);
  const part = await client.call('attachment.get', { id: last.attachment!.id });
  assert.equal(part.done, false);
  client.close();
});

test('the type is checked by the first bytes; over 10 MiB is refused with the limit gate; a gone image is not_found', async () => {
  const { client, daemon } = setup();
  await client.start();
  await assert.rejects(put(client, Buffer.from('not an image at all')), (e: { code?: string }) => e.code === 'bad_request');
  await assert.rejects(
    put(client, PNG, { bytes: 11 * 1024 * 1024 }),
    (e: { code?: string; gate?: string }) => e.code === 'refused' && e.gate === 'limit',
  );
  const r = await put(client, PNG);
  daemon.uploads.expire(r.attachment!.id);
  await assert.rejects(client.call('attachment.get', { id: r.attachment!.id }), (e: { code?: string }) => e.code === 'not_found');
  client.close();
});

test('a reply to an open question takes its images like any message', async () => {
  const { client, daemon } = setup();
  await client.start();
  const ask = daemon.chat.ask(AUD, 'Which?', 'founder');
  const r = await put(client, PNG);
  const sent = await client.call('chat.send', {
    agent: AUD,
    body: 'this one',
    parent: ask,
    attachments: [r.attachment!.id],
    request_id: 'op-a',
  });
  assert.ok(sent.request);
  client.close();
});

test("a task's text names only finished, kept images; an agent only one a task already shows", async () => {
  const s = setup();
  s.daemon.tasks.verbs = true;
  await s.client.start();
  const agent = second(s.daemon, s.net, { role: 'agent', client: AUD });
  await agent.start();
  const img = (await put(s.client, PNG)).attachment!.id;
  const ref = (id: string) => `![the button](attachment:${id})`;
  const code = (e: unknown) => (e as { data?: { code?: string; field?: string; gate?: string } }).data;
  const fail = (p: Promise<unknown>) => p.then(() => assert.fail('accepted'), code);
  const made = await s.client.call('task.new', { owner: AUD, title: 't', body: 'plain', request_id: 'n1' });
  const task = made.task as number;
  assert.equal((await fail(s.client.call('task.new', { owner: AUD, title: 't', body: ref('0000000000000000'), request_id: 'n2' })))?.code, 'not_found');
  assert.equal((await fail(s.client.call('task.comment.add', { task, text: ref('nope'), request_id: 'm1' })))?.field, 'text');
  assert.equal((await fail(s.client.call('task.comment.add', { task, text: ref(img).repeat(7), request_id: 'm2' })))?.field, 'text');
  assert.equal((await fail(agent.call('task.comment.add', { task, text: ref(img), request_id: 'm3' })))?.gate, 'role');
  await s.client.call('task.edit', { task, body: `cut off ${ref(img)}`, request_id: 'e1' });
  await agent.call('task.comment.add', { task, text: `from the brief ${ref(img)}`, request_id: 'm4' });
  const image = { id: img, name: 'a.png', mime: 'image/png', bytes: PNG.length };
  const mine = await s.client.call('task.get', { task });
  assert.deepEqual(mine.body_parts, [{ text: 'cut off ' }, { image, caption: 'the button' }]);
  assert.deepEqual(mine.memos[0]!.parts, [{ text: 'from the brief ' }, { image, caption: 'the button' }]);
  const theirs = await agent.call('task.get', { task });
  assert.match(String((theirs.memos[0]!.parts![1] as { path?: string }).path), /^\/.*\.png$/);
  s.daemon.uploads.expire(img);
  assert.deepEqual((await s.client.call('task.get', { task })).body_parts![1], { image: { id: img }, caption: 'the button', gone: true });
  await s.client.call('task.edit', { task, body: `still cut off ${ref(img)}`, request_id: 'e2' }); // a kept reference is not checked again
  agent.close();
  s.client.close();
});
