import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

test('chat.search finds a message by text, and refuses a limit that is not a positive integer', async () => {
  const { client } = setup();
  await client.start();
  const hit = await client.call('chat.search', { query: 'contract fixtures', limit: 1 });
  assert.deepEqual(
    hit.messages.map((m) => [m.agent, m.scope]),
    [['handline.architect', 'mine']],
  );
  for (const limit of [-1, 0, 1.5, '3' as never]) {
    await assert.rejects(client.call('chat.search', { query: 'contract', limit }), (e: RpcError) => e.data?.field === 'limit');
  }
});
