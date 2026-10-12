import { test } from 'node:test';
import assert from 'node:assert/strict';
import { setup } from '../src/helpers.ts';

const AUD = 'handline.auditor';
// A 1x1 PNG (70 bytes), a PNG header padded out to a chosen size, and bytes that are not an image.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const png = (n: number) => Buffer.concat([PNG.subarray(0, 8), Buffer.alloc(n - 8, 1)]);
const put = (client: ReturnType<typeof setup>['client'], b: Buffer, p: Record<string, unknown> = {}) =>
  client.call('attachment.put', { name: 'a.png', mime: 'image/png', bytes: b.length, data: b.toString('base64'), request_id: `op-${Math.random()}`, ...p } as never);

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
  await assert.rejects(client.call('chat.send', { agent: AUD, body: 'again', attachments: [r.attachment!.id], request_id: 'op-t' }), /already sent|unknown/);
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
  await assert.rejects(put(client, big.subarray(0, 10), { bytes: big.length, upload: first.upload, offset: 5, request_id: 'c2' }), (e: { data?: { field?: string; received?: number } }) => e.data?.field === 'offset' && e.data?.received === 512 * 1024);
  const last = await put(client, big.subarray(512 * 1024), { bytes: big.length, upload: first.upload, offset: 512 * 1024, request_id: 'c3' });
  assert.equal(last.attachment?.bytes, big.length);
  const part = await client.call('attachment.get', { id: last.attachment!.id });
  assert.equal(part.done, false);
  client.close();
});

test('the type is checked by the first bytes; over 10 MiB is refused with the limit gate; a gone image is not_found', async () => {
  const { client, daemon } = setup();
  await client.start();
  await assert.rejects(put(client, Buffer.from('not an image at all')), (e: { code?: string }) => e.code === 'bad_request');
  await assert.rejects(put(client, PNG, { bytes: 11 * 1024 * 1024 }), (e: { code?: string; gate?: string }) => e.code === 'refused' && e.gate === 'limit');
  const r = await put(client, PNG);
  daemon.attachments.expire(r.attachment!.id);
  await assert.rejects(client.call('attachment.get', { id: r.attachment!.id }), (e: { code?: string }) => e.code === 'not_found');
  client.close();
});

test('a reply to an open question takes its images like any message', async () => {
  const { client, daemon } = setup();
  await client.start();
  const ask = daemon.chat.ask(AUD, 'Which?', 'founder');
  const r = await put(client, PNG);
  const sent = await client.call('chat.send', { agent: AUD, body: 'this one', parent: ask, attachments: [r.attachment!.id], request_id: 'op-a' });
  assert.ok(sent.request);
  client.close();
});
