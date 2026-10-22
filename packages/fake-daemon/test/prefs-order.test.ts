import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '#src/helpers.ts';

const isBad = (e: RpcError) => e.code === 'bad_request' && e.data?.field === 'changes.nav.order';

test('nav.order is stored whole, refuses a name twice or no agent, and loses a retired agent', async () => {
  const { client, daemon } = setup();
  await client.start();
  const order = async () => (await client.call('prefs.get', {})).nav.order;
  assert.deepEqual(await order(), []);
  const set = (names: string[], id: string) => client.call('prefs.set', { changes: { nav: { order: names } }, request_id: id });
  await set(['handline.term-build', 'handline.auditor'], 'ord-1');
  assert.deepEqual(await order(), ['handline.term-build', 'handline.auditor']);
  await assert.rejects(set(['handline.auditor', 'handline.auditor'], 'ord-2'), isBad);
  await assert.rejects(set(['no-such-agent'], 'ord-3'), isBad);
  await assert.rejects(set('handline.auditor' as never, 'ord-4'), isBad);
  assert.deepEqual(await order(), ['handline.term-build', 'handline.auditor']);
  daemon.sessions.get('handline.auditor')!.open = false;
  await client.call('agent.retire', { agent: 'handline.auditor', request_id: 'ord-5' });
  assert.deepEqual(await order(), ['handline.term-build']);
  client.close();
});
