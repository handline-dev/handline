import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DaemonClient, memoryPair } from '@handline/client';

/** A fake daemon that answers hello and records the params it got. */
function helloParams(key: () => string | null) {
  const got: Array<Record<string, unknown>> = [];
  const client = new DaemonClient({
    client: 'electron',
    key,
    reconnect: false,
    connect: async () => {
      const [c, s] = memoryPair();
      s.onmessage = (m) => {
        const { id, params } = JSON.parse(m);
        got.push(params);
        s.send(JSON.stringify({ jsonrpc: '2.0', id, result: { protocol: 1, min_client: 0 } }));
      };
      return c;
    },
  });
  return { got, client };
}

test('no key, none sent', async () => {
  const { got, client } = helloParams(() => null);
  await client.start();
  client.close();
  assert.ok(got.length > 0);
  assert.ok(got.every((p) => !('key' in p) || p.key === undefined));
});
