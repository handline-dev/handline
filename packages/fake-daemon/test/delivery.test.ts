import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

const AUD = 'handline.auditor'; // a structured session
const TERM = 'handline.term-build'; // a terminal agent
const states = async (client: ReturnType<typeof setup>['client'], agent = AUD) =>
  (await client.call('chat.messages', { agent, scope: 'mine' })).messages.map((m) => `${m.body}:${m.receipt?.state}`);

test('a message to an idle structured agent is sent, then confirmed by the CLI echo', async () => {
  const { client, daemon } = setup();
  await client.start();
  const r = await client.call('chat.send', { agent: AUD, body: 'hello', request_id: 'op-1' });
  assert.equal(r.receipt?.state, 'sent');
  assert.match(r.receipt!.uuid, /^[0-9a-f]{8}-[0-9a-f]{4}-/);
  assert.deepEqual(await states(client), ['hello:sent']);
  const m = (await client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages[0]!;
  assert.equal(m.landing, 'typed');
  daemon.chat.tick();
  assert.deepEqual(await states(client), ['hello:confirmed']);
  client.close();
});

test('a repeat returns the receipt as it is now, writes nothing, and other params are bad_request', async () => {
  const { client, daemon } = setup();
  await client.start();
  const first = await client.call('chat.send', { agent: AUD, body: 'once', request_id: 'op-2' });
  daemon.chat.tick();
  const again = await client.call('chat.send', { agent: AUD, body: 'once', request_id: 'op-2' });
  assert.equal(again.replayed, true);
  assert.equal(again.request, first.request);
  assert.deepEqual(again.receipt, { state: 'confirmed', uuid: first.receipt!.uuid });
  assert.equal((await client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages.length, 1);
  await assert.rejects(
    client.call('chat.send', { agent: AUD, body: 'other', request_id: 'op-2' }),
    (e: RpcError) => e.data?.field === 'request_id',
  );
  client.close();
});

test('while a turn runs a message is queued by default and written with delivery now; delivery is now or queue', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.sessions.get(AUD)!.state = 'running';
  const q = await client.call('chat.send', { agent: AUD, body: 'later', request_id: 'op-3' });
  const n = await client.call('chat.send', { agent: AUD, body: 'now', request_id: 'op-4', delivery: 'now' });
  assert.equal(q.receipt?.state, 'queued');
  assert.equal(n.receipt?.state, 'sent');
  assert.equal(
    (await client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages.find((m) => m.body === 'later')!.landing,
    'queued',
  );
  await assert.rejects(
    client.call('chat.send', { agent: AUD, body: 'x', request_id: 'op-5', delivery: 'soon' as 'now' }),
    (e: RpcError) => e.data?.field === 'delivery',
  );
  client.close();
});

test('chat.send_now writes a queued message, and the turn ending writes the next one; a second send_now is refused', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.sessions.get(AUD)!.state = 'running';
  const a = await client.call('chat.send', { agent: AUD, body: 'a', request_id: 'op-6' });
  await client.call('chat.send', { agent: AUD, body: 'b', request_id: 'op-7' });
  assert.equal((await client.call('chat.send_now', { request: a.request, request_id: 'op-6n' })).receipt?.state, 'sent');
  await assert.rejects(client.call('chat.send_now', { request: a.request, request_id: 'op-6m' }), (e: RpcError) => e.gate === 'state');
  daemon.chat.turnEnded(AUD);
  assert.deepEqual((await states(client)).sort(), ['a:sent', 'b:sent']);
  client.close();
});

test('chat.cancel withdraws a queued message without confirm and drops it; its request_id is not brought back; a written one needs confirm', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.sessions.get(AUD)!.state = 'running';
  const q = await client.call('chat.send', { agent: AUD, body: 'oops', request_id: 'op-8' });
  const w = await client.call('chat.send', { agent: AUD, body: 'written', request_id: 'op-9', delivery: 'now' });
  await client.call('chat.cancel', { request: q.request, request_id: 'op-8c' });
  assert.deepEqual(await states(client), ['written:sent']);
  await assert.rejects(client.call('chat.send', { agent: AUD, body: 'oops', request_id: 'op-8' }), (e: RpcError) => e.gate === 'state');
  await assert.rejects(client.call('chat.cancel', { request: w.request, request_id: 'op-9c' }), (e: RpcError) => e.gate === 'confirm');
  client.close();
});

test('a message lost after it was written is unknown, stays unknown on a repeat, and is never written again', async () => {
  const { client, daemon } = setup();
  await client.start();
  const r = await client.call('chat.send', { agent: AUD, body: 'in flight', request_id: 'op-10' });
  daemon.chat.lose(AUD);
  daemon.chat.tick();
  assert.deepEqual(await states(client), ['in flight:unknown']);
  assert.equal((await client.call('chat.messages', { agent: AUD, scope: 'mine' })).messages[0]!.landing, 'typed');
  const again = await client.call('chat.send', { agent: AUD, body: 'in flight', request_id: 'op-10' });
  assert.equal(again.receipt?.state, 'unknown');
  assert.equal(again.receipt?.uuid, r.receipt?.uuid);
  client.close();
});

test('a terminal agent accepts delivery and has no receipt', async () => {
  const { client } = setup();
  await client.start();
  const r = await client.call('chat.send', { agent: TERM, body: 'hi', request_id: 'op-11', delivery: 'now' });
  assert.equal(r.receipt, undefined);
  assert.equal((await client.call('chat.messages', { agent: TERM, scope: 'mine' })).messages[0]!.receipt, undefined);
  client.close();
});

test('a turn that ends through the session writes the held message, and a session that closes loses what was not echoed', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.sessions.get(AUD)!.state = 'running';
  await client.call('chat.send', { agent: AUD, body: 'held', request_id: 'op-12' });
  assert.deepEqual(await states(client), ['held:queued']);
  await client.call('session.interrupt', { agent: AUD, request_id: 'op-12i' });
  assert.deepEqual(await states(client), ['held:sent']);
  await client.call('session.stop', { agent: AUD, mode: 'close', request_id: 'op-12s' });
  assert.deepEqual(await states(client), ['held:unknown']);
  client.close();
});
