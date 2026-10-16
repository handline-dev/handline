import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

test('agent.retire refuses an open session, then retires the agent and it is gone', async () => {
  const { client, daemon } = setup();
  await client.start();
  const name = 'handline.auditor';
  const s = daemon.sessions.get(name)!;
  s.open = true;
  await assert.rejects(
    client.call('agent.retire', { agent: name, request_id: 'ret-1' }),
    (e: RpcError) => e.code === 'refused' && e.gate === 'state',
  );
  s.open = false;
  const r = await client.call('agent.retire', { agent: name, request_id: 'ret-2' });
  assert.equal(r.said, `${name} is retired.`);
  const names = (await client.call('agents.list', {})).agents.map((a: { name: string }) => a.name);
  assert.ok(!names.includes(name), names.join(', '));
  client.close();
});
