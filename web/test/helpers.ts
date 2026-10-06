import { DaemonClient, type ClientOptions } from '../src/client.ts';
import { memoryPair } from '../src/transport.ts';
import { FakeDaemon, type FakeDaemonOptions } from '../fake-daemon/daemon.ts';
import type { Role } from '../src/protocol.ts';

export function setup(opts: { daemon?: FakeDaemonOptions; role?: Role; client?: string; reconnect?: ClientOptions['reconnect'] } = {}) {
  const daemon = new FakeDaemon(opts.daemon);
  const net = { down: false }; // while down, connecting fails like a refused socket
  return { daemon, net, client: second(daemon, net, opts) };
}

/** Another client on the same daemon. */
export function second(daemon: FakeDaemon, net: { down: boolean }, opts: { role?: Role; client?: string; reconnect?: ClientOptions['reconnect'] } = {}) {
  return new DaemonClient({
    client: opts.client ?? 'web-test',
    reconnect: opts.reconnect ?? { minMs: 5, maxMs: 20 },
    connect: async () => { if (net.down) throw new Error('refused'); const [a, b] = memoryPair(); daemon.accept(b, opts.role); return a; },
  });
}

export const until = async (cond: () => boolean, what = 'condition') => {
  for (let i = 0; i < 400; i++) { if (cond()) return; await new Promise((r) => setTimeout(r, 5)); }
  throw new Error(`timed out waiting for ${what}`);
};
export const flush = () => new Promise((r) => setTimeout(r, 15));
