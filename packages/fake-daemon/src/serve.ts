// Runs the fake daemon on a Unix socket (lines) and a WebSocket port (for the browser), LAN/localhost only.
//   node serve.ts [--socket PATH] [--network-socket PATH] [--ws PORT] [--host 127.0.0.1] [--autoplay MS]
import type https from 'node:https';
import net from 'node:net';
import type { Transport } from '@handline/client';
import { wrapSocket } from '@handline/client-node';
import { FakeDaemon } from './daemon.ts';
import { wsServer } from './ws-server.ts';

/** `listen` is a Unix socket path, or a TCP port (lines either way). `network` serves it as the paired-device listener does (for contract/conformance.py --as unpaired=PATH). */
export function serveSocket(daemon: FakeDaemon, listen: string | number, network = false): net.Server {
  const server = net.createServer((sock) => (network ? daemon.acceptNetwork(wrapSocket(sock)) : daemon.accept(wrapSocket(sock))));
  server.listen(listen as never);
  return server;
}
export function serveWs(daemon: FakeDaemon, port: number, host = '127.0.0.1', tls?: https.ServerOptions, network = false) {
  const server = wsServer((t: Transport) => (network ? daemon.acceptNetwork(t) : daemon.accept(t)), tls, network ? '/rpc' : undefined);
  server.listen(port, host);
  return server;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const arg = (n: string, d: string) => { const i = process.argv.indexOf(`--${n}`); return i > 0 ? process.argv[i + 1] : d; };
  const daemon = new FakeDaemon();
  const ms = Number(arg('autoplay', '1000'));
  if (ms > 0) daemon.autoplay(ms);
  serveSocket(daemon, arg('socket', '/tmp/handline-fake-daemon.sock'));
  if (process.argv.includes('--network-socket')) serveSocket(daemon, arg('network-socket', ''), true);
  serveWs(daemon, Number(arg('ws', '7777')), arg('host', '127.0.0.1'));
  console.log(`fake daemon: socket ${arg('socket', '/tmp/handline-fake-daemon.sock')}, ws://${arg('host', '127.0.0.1')}:${arg('ws', '7777')}`);
}
