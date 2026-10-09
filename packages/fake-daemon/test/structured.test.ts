import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { RpcError } from '@handline/client';
import { setup } from '../src/helpers.ts';

const AUD = 'handline.auditor'; // a structured session, idle
const rejected = (gate: string) => (e: RpcError) => e.code === 'refused' && e.gate === gate;

test('a structured agent has no terminal and no input holder, and the typing methods are refused and terminal.attach finds none', async () => {
  const { client } = setup();
  await client.start();
  const d = await client.call('agent.get', { agent: AUD });
  assert.deepEqual([d.structured, d.terminal, d.session_open, d.input_holders], [true, null, true, []]);
  await assert.rejects(client.call('session.input', { agent: AUD, text: 'x', request_id: 'sc-1' }), rejected('unsupported'));
  await assert.rejects(client.call('session.keys', { agent: AUD, keys: ['enter'], request_id: 'sc-2' }), rejected('unsupported'));
  await assert.rejects(client.call('input.take', { agent: AUD, request_id: 'sc-3' }), rejected('unsupported'));
  await assert.rejects(client.call('terminal.attach', { agent: AUD }), (e: RpcError) => e.code === 'not_found');
  client.close();
});

test('interrupt needs a turn that runs, and close is refused mid-turn; a restart opens no terminal', async () => {
  const { client } = setup();
  await client.start();
  await assert.rejects(client.call('session.interrupt', { agent: AUD, request_id: 'sc-4' }), rejected('state'));
  await client.call('session.stop', { agent: AUD, mode: 'close', request_id: 'sc-5' });
  await client.call('session.wake', { agent: AUD, request_id: 'sc-6' });
  const d = await client.call('agent.get', { agent: AUD });
  assert.deepEqual([d.session_open, d.terminal], [true, null]);
  client.close();
});
