import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type ChatMessage, type Event, type RpcError } from '@handline/client';
import { setup, until } from '../src/helpers.ts';

const ARCH = 'handline.architect';

test('latency delays every call after hello, and clear puts it back', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.knobs.latency(120);
  const t0 = Date.now();
  await client.call('agents.list', {});
  assert.ok(Date.now() - t0 >= 110, `took ${Date.now() - t0} ms`);
  daemon.knobs.clear();
  const t1 = Date.now();
  await client.call('agents.list', {});
  assert.ok(Date.now() - t1 < 100);
  client.close();
});

test('the handshake is never delayed, so a client can connect while the daemon is slow', async () => {
  const { client, daemon } = setup();
  daemon.knobs.latency(2000);
  const t0 = Date.now();
  await client.start();
  assert.ok(Date.now() - t0 < 500);
  client.close();
});

test('refuse answers with the gate, reason and hint given, until cleared', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.knobs.refuse('agent.get', 'wall', 'the usage limit is hit', 'wait for the reset');
  for (let i = 0; i < 2; i++)
    await assert.rejects(
      client.call('agent.get', { agent: ARCH }),
      (e: RpcError) =>
        e.code === 'refused' && e.gate === 'wall' && e.reason === 'the usage limit is hit' && e.hint === 'wait for the reset',
    );
  daemon.knobs.clear();
  assert.equal((await client.call('agent.get', { agent: ARCH })).name, ARCH);
  client.close();
});

test('refuse with a count lets the next call through, and only the named method is refused', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.knobs.refuse('agents.list', 'state', 'no', undefined, 1);
  await assert.rejects(client.call('agents.list', {}), (e: RpcError) => e.gate === 'state');
  assert.equal((await client.call('agents.list', {})).agents.length, 3);
  daemon.knobs.refuse('agents.list', 'state', 'no');
  assert.equal((await client.call('agent.get', { agent: ARCH })).name, ARCH);
  client.close();
});

test('chat: messages come back newest first with a flag for more, and the transcript with the working line', async () => {
  const { client } = setup();
  await client.start();
  const m = await client.call('chat.messages', { agent: ARCH, scope: 'mine', limit: 2 });
  assert.equal(m.messages.length, 2);
  assert.equal(m.more, true);
  assert.ok(m.messages[0]!.id > m.messages[1]!.id);
  const t = await client.call('chat.transcript', { agent: ARCH });
  assert.ok(t.items.length >= 3);
  assert.equal(t.working.turn_running, false);
  client.close();
});

test('chat: a send lands one step at a time on the chat topic, and ends answered', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.subscribe(['chat']);
  const seen: string[] = [];
  client.onEvent((e: Event) => {
    if (e.type === 'chat.message') seen.push((e.data as unknown as ChatMessage).landing);
  });
  const { request } = await client.call('chat.send', { agent: ARCH, body: 'hi' });
  daemon.settle();
  await until(() => seen.includes('answered'), 'the answer');
  assert.deepEqual(seen, ['queued', 'typed', 'delivered', 'answered']);
  assert.ok(request > 0);
  client.close();
});

test('chat: dismiss only once', async () => {
  const { client, daemon } = setup();
  await client.start();
  daemon.chat.ask(ARCH, 'which?', 'web-test');
  const ask = (await client.call('chat.messages', { agent: ARCH, scope: 'mine' })).messages.find(
    (x) => x.kind === 'question' && !x.answer,
  )!;
  await client.call('chat.dismiss', { request: ask.id });
  await assert.rejects(client.call('chat.dismiss', { request: ask.id }), (e: RpcError) => e.code === 'refused');
  client.close();
});

test('a daemon started without chat does not list or serve the chat methods', async () => {
  const { client } = setup({ daemon: { chat: false } });
  const h = await client.start();
  assert.ok(!h.methods.some((m) => m.name.startsWith('chat.')));
  assert.equal(h.capabilities.chat, false);
  await assert.rejects(client.call('chat.messages', { agent: ARCH, scope: 'mine' }), (e: RpcError) => e.gate === 'unsupported');
  client.close();
});
