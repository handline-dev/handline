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

/** A fake daemon that is the founder's only to a hello carrying the key `good`, and records every hello. */
function founderDaemon(key: () => string | null, good: string) {
  const roles: string[] = [];
  let live: ReturnType<typeof memoryPair>[1] | null = null;
  const client = new DaemonClient({
    client: 'electron',
    key,
    founder: true,
    reconnect: { minMs: 5, maxMs: 10 },
    connect: async () => {
      const [c, s] = memoryPair();
      live = s;
      s.onmessage = (m) => {
        const { id, params } = JSON.parse(m);
        const role = params.key === good ? 'founder' : 'anonymous';
        roles.push(role);
        s.send(JSON.stringify({ jsonrpc: '2.0', id, result: { protocol: 1, min_client: 0, role } }));
      };
      return c;
    },
  });
  return { roles, client, drop: () => live?.close() };
}

test('a hello that is not the founder is retried until the key is found, then the first write goes', async () => {
  let key: string | null = null;
  const { roles, client } = founderDaemon(() => key, 'k1');
  setTimeout(() => (key = 'k1'), 30);
  for (let tries = 0; tries < 100; tries++) {
    try {
      await client.start(); // launch retries a start that fails, as main.ts does
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 5));
    }
  }
  client.close();
  assert.equal(roles.at(-1), 'founder');
  assert.ok(roles.slice(0, -1).every((r) => r === 'anonymous'));
  assert.equal(client.hello?.role, 'founder');
});

test('a dropped connection says hello with the key again before anything else is sent', async () => {
  const { roles, client, drop } = founderDaemon(() => 'k1', 'k1');
  await client.start();
  drop();
  await new Promise<void>((ok) => {
    const t = setInterval(() => roles.length >= 2 && client.state === 'open' && (clearInterval(t), ok()), 5);
  });
  client.close();
  assert.deepEqual(roles, ['founder', 'founder']);
});
