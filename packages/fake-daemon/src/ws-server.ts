// A minimal WebSocket server (text frames only, RFC 6455) so a browser can reach the fake daemon
// without a dependency. Not for anything but the fake.
import crypto from 'node:crypto';
import http from 'node:http';
import https from 'node:https';
import type { Duplex } from 'node:stream';
import type { Transport } from '@handline/client';
import { readFrame } from '@handline/client-node';

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';

function frame(text: string): Buffer {
  const body = Buffer.from(text);
  const n = body.length;
  const head =
    n < 126
      ? Buffer.from([0x81, n])
      : n < 65536
        ? Buffer.from([0x81, 126, n >> 8, n & 255])
        : Buffer.concat([Buffer.from([0x81, 127]), Buffer.alloc(8)]);
  if (n >= 65536) head.writeBigUInt64BE(BigInt(n), 2);
  return Buffer.concat([head, body]);
}

function wrap(sock: Duplex): Transport {
  const t: Transport = {
    onmessage: null,
    onclose: null,
    send: (m) => {
      sock.write(frame(m));
    },
    close: () => {
      sock.end(Buffer.from([0x88, 0]));
    },
  };
  let buf = Buffer.alloc(0);
  let parts: Buffer[] = []; // the pieces of a text message sent in several frames (a browser splits a large one)
  sock.on('data', (d: Buffer) => {
    buf = Buffer.concat([buf, d]);
    for (;;) {
      const f = readFrame(buf);
      if (!f) return;
      const { fin, op, payload } = f;
      buf = f.rest;
      if (op === 1 || op === 0) {
        parts.push(payload);
        if (fin) {
          t.onmessage?.(Buffer.concat(parts).toString());
          parts = [];
        }
      }
      else if (op === 8) {
        sock.end();
        return;
      } else if (op === 9) sock.write(Buffer.concat([Buffer.from([0x8a, payload.length]), payload]));
    }
  });
  sock.on('close', () => t.onclose?.());
  sock.on('error', () => {});
  return t;
}

/** `path` limits the upgrade to one URL path, as the real listener's /rpc does; without it any path upgrades. */
export function wsServer(onConnect: (t: Transport) => void, tls?: https.ServerOptions, path?: string): http.Server {
  const refuse = (_: http.IncomingMessage, res: http.ServerResponse) => {
    res.statusCode = 426;
    res.end('websocket only');
  };
  const server = tls ? https.createServer(tls, refuse) : http.createServer(refuse);
  server.on('upgrade', (req, sock) => {
    const key = req.headers['sec-websocket-key'];
    if (typeof key !== 'string' || (path !== undefined && req.url !== path)) {
      sock.destroy();
      return;
    }
    const accept = crypto
      .createHash('sha1')
      .update(key + GUID)
      .digest('base64');
    sock.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
    onConnect(wrap(sock));
  });
  return server;
}
