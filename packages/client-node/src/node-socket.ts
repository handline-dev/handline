// Node only (Electron main, tests, tools): the daemon's Unix socket, one JSON object per line.
import net from 'node:net';
import { LineSplitter, type Transport } from '@handline/client';

export function wrapSocket(sock: net.Socket): Transport {
  const split = new LineSplitter();
  const t: Transport = {
    onmessage: null,
    onclose: null,
    send: (m) => {
      sock.write(m + '\n');
    },
    close: () => {
      sock.destroy();
    },
  };
  sock.setEncoding('utf8');
  sock.on('data', (d: string) => {
    for (const line of split.push(d)) t.onmessage?.(line);
  });
  sock.on('close', () => t.onclose?.());
  sock.on('error', () => {});
  return t;
}

/** A Unix socket path, or a TCP port on localhost. */
export function unixSocketTransport(target: string | number): Promise<Transport> {
  return new Promise((resolve, reject) => {
    const sock = typeof target === 'number' ? net.createConnection(target, '127.0.0.1') : net.createConnection(target);
    sock.once('connect', () => resolve(wrapSocket(sock)));
    sock.once('error', reject);
  });
}
