import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AddressInfo } from 'node:net';
import { DaemonClient, webSocketTransport } from '@handline/client';
import { unixSocketTransport } from '@handline/client-node';
import { FakeDaemon } from '../src/daemon.ts';
import { serveSocket, serveWs } from '../src/serve.ts';
import { until } from '../src/helpers.ts';

const A = 'handline.term-build';

async function drive(client: DaemonClient, daemon: FakeDaemon) {
  await client.start();
  await client.subscribe(['agents']);
  const view = await client.attachTerminal(A);
  await client.startSession(A);
  await client.sendInput(A, 'hello');
  await until(() => view.text().includes('> hello'), 'echo over the wire');
  daemon.settle();
  await until(() => view.text().includes('Do you want to proceed?'), 'prompt over the wire');
  const [p] = await client.openPrompts(A);
  await client.answerPrompt(A, p!.id, '1');
  daemon.settle();
  await until(() => view.text().includes('● Pushed. Done.'), 'finish');
  client.close();
}

test('the same client works over the line socket (TCP here; the Unix path is the same framing)', async () => {
  const daemon = new FakeDaemon();
  const server = serveSocket(daemon, 0);
  await new Promise((r) => server.once('listening', r));
  const port = (server.address() as AddressInfo).port;
  await drive(new DaemonClient({ client: 'sock', reconnect: false, connect: () => unixSocketTransport(port) }), daemon);
  daemon.stop(); server.close();
});

test('the same client works over a WebSocket (browser path)', async () => {
  const daemon = new FakeDaemon();
  const server = serveWs(daemon, 0);
  await new Promise((r) => server.once('listening', r));
  const url = `ws://127.0.0.1:${(server.address() as AddressInfo).port}`;
  await drive(new DaemonClient({ client: 'ws', reconnect: false, connect: () => webSocketTransport(url) }), daemon);
  daemon.stop(); server.close();
});
