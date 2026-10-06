// A WebSocket client for a listener with a self-signed certificate (Node only: tests, Electron main, tools).
// The certificate is trusted by one thing only: its SHA-256 fingerprint, the one pairing handed over.
import crypto from 'node:crypto';
import tls from 'node:tls';
import type { Transport } from '@handline/client';

const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11';
const normalize = (fp: string) => fp.replace(/[^0-9a-f]/gi, '').toLowerCase();

function maskedFrame(op: number, payload: Buffer): Buffer {
  const n = payload.length;
  const head = n < 126 ? Buffer.from([0x80 | op, 0x80 | n]) : n < 65536 ? Buffer.from([0x80 | op, 0x80 | 126, n >> 8, n & 255])
    : Buffer.concat([Buffer.from([0x80 | op, 0x80 | 127]), Buffer.alloc(8)]);
  if (n >= 65536) head.writeBigUInt64BE(BigInt(n), 2);
  const mask = crypto.randomBytes(4);
  const body = Buffer.from(payload);
  for (let i = 0; i < body.length; i++) body[i] ^= mask[i & 3]!;
  return Buffer.concat([head, mask, body]);
}

export function pinnedWebSocketTransport(url: string, fingerprint: string): Promise<Transport> {
  const u = new URL(url);
  if (u.protocol !== 'wss:') return Promise.reject(new Error('a pinned connection is wss'));
  return new Promise((resolve, reject) => {
    const sock = tls.connect({ host: u.hostname, port: Number(u.port || 443), servername: u.hostname, rejectUnauthorized: false, minVersion: 'TLSv1.3' });
    const fail = (why: string) => { sock.destroy(); reject(new Error(why)); };
    sock.once('error', (e) => fail(`cannot connect to ${url}: ${e.message}`));
    sock.once('secureConnect', () => {
      const cert = sock.getPeerCertificate();
      const got = cert?.raw ? crypto.createHash('sha256').update(cert.raw).digest('hex') : '';
      if (got !== normalize(fingerprint)) { fail(`the certificate of ${u.host} is not the one that was paired (fingerprint ${got})`); return; }
      const key = crypto.randomBytes(16).toString('base64');
      sock.write(`GET ${u.pathname}${u.search} HTTP/1.1\r\nHost: ${u.host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
      let buf = Buffer.alloc(0), upgraded = false, parts: Buffer[] = [];
      const t: Transport = {
        onmessage: null, onclose: null,
        send: (m) => { sock.write(maskedFrame(1, Buffer.from(m))); },
        close: () => { sock.end(maskedFrame(8, Buffer.alloc(0))); },
      };
      sock.on('data', (d: Buffer) => {
        buf = Buffer.concat([buf, d]);
        if (!upgraded) {
          const end = buf.indexOf('\r\n\r\n');
          if (end < 0) return;
          const head = buf.subarray(0, end).toString();
          const accept = crypto.createHash('sha1').update(key + GUID).digest('base64');
          if (!/^HTTP\/1\.1 101/.test(head) || !head.includes(accept)) { fail(`${u.host} did not accept the WebSocket upgrade`); return; }
          upgraded = true; buf = buf.subarray(end + 4); resolve(t);
        }
        for (;;) {
          if (buf.length < 2) return;
          const fin = (buf[0]! & 128) !== 0, op = buf[0]! & 15;
          let len = buf[1]! & 127, off = 2;
          if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
          else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
          if (buf.length < off + len) return;
          const payload = buf.subarray(off, off + len);
          buf = buf.subarray(off + len);
          if (op === 1 || op === 0) { parts.push(Buffer.from(payload)); if (fin) { const m = Buffer.concat(parts).toString(); parts = []; t.onmessage?.(m); } }
          else if (op === 9) sock.write(maskedFrame(10, payload));
          else if (op === 8) { sock.end(); return; }
        }
      });
      sock.on('close', () => { if (upgraded) t.onclose?.(); else reject(new Error(`${u.host} closed the connection before the WebSocket upgrade`)); });
    });
  });
}
