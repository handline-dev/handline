import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

test('chat.locate takes a msg_ name or a bare hash and returns the transcript of its window', async () => {
  const { daemon, client } = setup();
  await client.start();
  const agent = 'locate.probe';
  daemon.chat.seedItem(agent, { kind: 'reply', text: 'before', at: '2026-10-01T00:00:00.000Z' });
  const id = daemon.chat.seed(agent, { body: 'here', from: 'human', to: agent, at: '2026-10-01T00:00:10.000Z' });
  daemon.chat.seedItem(agent, { kind: 'reply', text: 'after', at: '2026-10-01T00:00:20.000Z' });
  const ref = daemon.chat.find(id).ref!;
  for (const request of [`msg_${ref.slice(0, 7)}`, ref]) {
    const got = await client.call('chat.locate', { request });
    assert.equal(got.message.id, id);
    // the window reaches the newest message, so the span runs on to now
    assert.deepEqual(
      got.transcript.map((i) => i.text),
      ['after'],
    );
    assert.equal(got.transcript_more_older, false);
    assert.equal(got.transcript_more_newer, false);
  }
});

test('chat.locate refuses a # name, a hash shorter than 6, and a hash two messages begin', async () => {
  const { daemon, client } = setup();
  await client.start();
  const agent = 'locate.probe';
  for (const ref of ['abcdef000001', 'abcdef000002']) daemon.chat.seed(agent, { body: ref, from: 'human', to: agent, ref });
  for (const request of ['#12', 'msg_abcde', 'abcdef']) {
    await assert.rejects(
      client.call('chat.locate', { request }),
      (e: RpcError) => /pull request|hex characters|ambiguous/.test(e.message),
      request,
    );
  }
  const got = await client.call('chat.locate', { request: 'msg_abcdef000002' });
  assert.equal(got.message.body, 'abcdef000002');
});
