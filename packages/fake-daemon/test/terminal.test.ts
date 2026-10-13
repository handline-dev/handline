import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memoryPair } from '@handline/client';
import { FakeDaemon } from '../src/daemon.ts';

test('terminal.open without an agent is invalid params, -32602', async () => {
  const daemon = new FakeDaemon();
  const [a, b] = memoryPair();
  daemon.accept(b);
  const ask = (id: number, method: string, params: object) =>
    new Promise<{ error?: { code: number; data: { code: string; field: string } } }>((ok) => {
      a.onmessage = (m) => ok(JSON.parse(m));
      a.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
    });
  await ask(1, 'hello', { client: 'c', protocol: 1 });
  const { error } = await ask(2, 'terminal.open', {});
  assert.equal(error?.code, -32602);
  assert.deepEqual([error?.data.code, error?.data.field], ['bad_request', 'agent']);
  a.close();
});
