import { test } from 'node:test';
import net, { type AddressInfo } from 'node:net';
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
  daemon.stop();
  server.close();
});

test('the same client works over a WebSocket (browser path)', async () => {
  const daemon = new FakeDaemon();
  const server = serveWs(daemon, 0);
  await new Promise((r) => server.once('listening', r));
  const url = `ws://127.0.0.1:${(server.address() as AddressInfo).port}`;
  await drive(new DaemonClient({ client: 'ws', reconnect: false, connect: () => webSocketTransport(url) }), daemon);
  daemon.stop();
  server.close();
});

test('a request split over several WebSocket frames (a browser does it for a large one) is answered as one', async () => {
  const daemon = new FakeDaemon();
  const server = serveWs(daemon, 0);
  await new Promise((r) => server.once('listening', r));
  const sock = net.connect((server.address() as AddressInfo).port, '127.0.0.1');
  let got = '';
  sock.on('data', (d) => void (got += d.toString('latin1')));
  sock.write(`GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ${Buffer.from('the sample nonce').toString('base64')}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
  await until(() => got.includes('101'), 'the upgrade');
  const text = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'hello', params: { client: 'frames', protocol: 1 } });
  const maskedFrame = (first: number, part: string) => {
    const body = Buffer.from(part);
    return Buffer.concat([Buffer.from([first, 0x80 | body.length, 0, 0, 0, 0]), body]); // a zero mask leaves the bytes as they are
  };
  const cut = Math.floor(text.length / 2);
  sock.write(maskedFrame(0x01, text.slice(0, cut))); // text frame, not final
  sock.write(maskedFrame(0x80, text.slice(cut))); // continuation, final
  await until(() => got.includes('"id":1'), 'the reply to the whole request');
  sock.destroy();
  daemon.stop();
  server.close();
});
