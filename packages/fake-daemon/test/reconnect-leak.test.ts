import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DaemonClient, memoryPair, type Transport } from '@handline/client';
import { FakeDaemon } from '../src/daemon.ts';
import { until } from '../src/helpers.ts';

// A daemon that hangs up on every hello while `down`, as one out of descriptors does, then serves again.
function flaky(daemon: FakeDaemon) {
  const made: Transport[] = [];
  const net = { down: false, connects: 0, open: () => made.filter((t) => !(t as Transport & { closed: boolean }).closed).length };
  const connect = async (): Promise<Transport> => {
    // Loops that double on each failure would run the process out of memory: past this, a connect never finishes.
    if (++net.connects > 100) return new Promise<Transport>(() => {});
    const [client, server] = memoryPair();
    made.push(client);
    if (net.down) {
      server.onmessage = () => server.close();
    } else {
      daemon.accept(server);
    }
    return client;
  };
  return { net, connect };
}

test('a daemon that hangs up during hello leaves one connection, not one per retry', async () => {
  const daemon = new FakeDaemon();
  const { net, connect } = flaky(daemon);
  const client = new DaemonClient({ client: 'electron', connect, reconnect: { minMs: 1, maxMs: 2 } });
  await client.start();
  net.down = true;
  daemon.dropConnections();
  await until(() => net.connects > 12, 'retries while down');
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(net.connects < 60, `${net.connects} connects in 20 ms: more than one loop is retrying`);
  net.down = false;
  await until(() => client.state === 'open', 'back');
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(net.open(), 1, `connections left open: ${net.open()} after ${net.connects} connects`);
  client.close();
});

test('a start that fails part way closes its connection and starts no loop of its own', async () => {
  const daemon = new FakeDaemon();
  const { net, connect } = flaky(daemon);
  net.down = true;
  const client = new DaemonClient({ client: 'electron', connect, reconnect: { minMs: 1, maxMs: 2 } });
  await assert.rejects(client.start());
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(net.connects, 1, 'only the caller retries a failed start');
  assert.equal(net.open(), 0);
  client.close();
});
