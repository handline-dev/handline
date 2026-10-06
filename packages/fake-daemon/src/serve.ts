// Runs the fake daemon on a Unix socket (lines) and a WebSocket port (for the browser), LAN/localhost only.
//   node fake-daemon/serve.ts [--socket PATH] [--ws PORT] [--host 127.0.0.1] [--autoplay MS]
import type https from 'node:https';
import net from 'node:net';
import type { Transport } from '@handline/client';
import { wrapSocket } from '@handline/client-node';
import { FakeDaemon } from './daemon.ts';
import { wsServer } from './ws-server.ts';

/** `listen` is a Unix socket path, or a TCP port (lines either way). */
export function serveSocket(daemon: FakeDaemon, listen: string | number): net.Server {
  const server = net.createServer((sock) => daemon.accept(wrapSocket(sock)));
  server.listen(listen as never);
  return server;
}
export function serveWs(daemon: FakeDaemon, port: number, host = '127.0.0.1', tls?: https.ServerOptions, network = false) {
  const server = wsServer((t: Transport) => (network ? daemon.acceptNetwork(t) : daemon.accept(t)), tls);
  server.listen(port, host);
  return server;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const arg = (n: string, d: string) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
  const daemon = new FakeDaemon();
  const ms = Number(arg('autoplay', '1000'));
  if (ms > 0) daemon.autoplay(ms);
  serveSocket(daemon, arg('socket', '/tmp/handline-fake-daemon.sock'));
  serveWs(daemon, Number(arg('ws', '7777')), arg('host', '127.0.0.1'));
  console.log(`fake daemon: socket ${arg('socket', '/tmp/handline-fake-daemon.sock')}, ws://${arg('host', '127.0.0.1')}:${arg('ws', '7777')}`);
}
