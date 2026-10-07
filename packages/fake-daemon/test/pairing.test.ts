import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { AddressInfo } from 'node:net';
import type { RpcError } from '@handline/client';
import { DaemonClient, listenerUrl, parsePairingUri, redeemPairing } from '@handline/client';
import { pinnedWebSocketTransport } from '@handline/client-node';
import { FakeDaemon } from '../src/daemon.ts';
import { serveWs } from '../src/serve.ts';

/** One self-signed certificate for the whole file: generating a key per test made a loaded run spend its 10 s file budget in openssl. */
let identity: { cert: Buffer; key: Buffer } | null | undefined;
function certificate() {
  if (identity !== undefined) return identity;
  const dir = fs.mkdtempSync(path.join(process.env.TMPDIR ?? os.tmpdir(), 'pair-'));
  try {
    execFileSync(
      'openssl',
      [
        'req',
        '-x509',
        '-newkey',
        'ec',
        '-pkeyopt',
        'ec_paramgen_curve:prime256v1',
        '-nodes',
        '-days',
        '1',
        '-subj',
        '/CN=localhost',
        '-keyout',
        `${dir}/k.pem`,
        '-out',
        `${dir}/c.pem`,
      ],
      { stdio: 'ignore' },
    );
    identity = { cert: fs.readFileSync(`${dir}/c.pem`), key: fs.readFileSync(`${dir}/k.pem`) };
  } catch {
    identity = null;
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  return identity;
}

/** The fake as the network listener: wss with a self-signed certificate, paired devices only. */
async function listener(t: { skip(why: string): void; after(fn: () => void): void }) {
  const tls = certificate();
  if (!tls) {
    t.skip('openssl is not available');
    return null;
  }
  const { cert, key } = tls;
  const daemon = new FakeDaemon();
  const server = serveWs(daemon, 0, '127.0.0.1', { cert, key }, true);
  await new Promise((ok) => server.once('listening', ok));
  t.after(() => {
    daemon.stop();
    server.close();
  });
  const host = `127.0.0.1:${(server.address() as AddressInfo).port}`;
  const fingerprint = crypto.createHash('sha256').update(new crypto.X509Certificate(cert).raw).digest('hex');
  return { daemon, host, fingerprint, connect: () => pinnedWebSocketTransport(listenerUrl(host), fingerprint) };
}

test('a pairing link with no fingerprint is accepted for a Tailscale host and served as ws', () => {
  for (const host of ['100.64.0.1:7788', '100.127.255.254:7788', 'box.tail1234.ts.net:7788'])
    assert.deepEqual(parsePairingUri(`handline://pair?host=${encodeURIComponent(host)}&code=K7Q2`), {
      host,
      fingerprint: null,
      code: 'K7Q2',
    });
  assert.equal(listenerUrl('100.64.0.1:7788', false), 'ws://100.64.0.1:7788/rpc');
});

test('a pairing link with no fingerprint is refused for any other host, with the reason', () => {
  for (const host of ['192.168.1.5:7788', '100.128.0.1:7788', '100.63.0.1:7788', 'example.com:7788', 'evil.ts.net.example.com:7788'])
    assert.throws(
      () => parsePairingUri(`handline://pair?host=${encodeURIComponent(host)}&code=K7Q2`),
      /no certificate fingerprint.*not a Tailscale address/,
    );
});

test('a pairing link with a fingerprint keeps wss and the pin, on a Tailscale host too', () => {
  const o = parsePairingUri('handline://pair?host=100.64.0.1%3A7788&fp=AB12&code=K7Q2');
  assert.deepEqual(o, { host: '100.64.0.1:7788', fingerprint: 'ab12', code: 'K7Q2' });
  assert.equal(listenerUrl(o.host), 'wss://100.64.0.1:7788/rpc');
});

test('a pairing uri names the listener, its certificate fingerprint and the code', () => {
  const o = parsePairingUri('handline://pair?host=10.0.0.2%3A7788&fp=AB12&code=K7Q2');
  assert.deepEqual(o, { host: '10.0.0.2:7788', fingerprint: 'ab12', code: 'K7Q2' });
  assert.throws(() => parsePairingUri('https://example.com/?code=1'), /not a handline pairing uri/);
});

test('a code is redeemed once for a token; with it hello makes the caller a member', async (t) => {
  const l = await listener(t);
  if (!l) return;
  const { token, device } = await redeemPairing(l.connect, l.daemon.issueCode(), 'phone');
  assert.match(token, /^hld_[0-9a-f]{64}$/);
  assert.match(device, /^dev_[0-9a-f]+$/);

  const paired = new DaemonClient({ connect: l.connect, client: 'web', token, reconnect: false });
  const hello = await paired.start();
  assert.equal(hello.role, 'member');
  assert.equal(hello.transport, 'network');
  assert.deepEqual(hello.caller, { kind: 'device', device, name: 'phone' });
  assert.ok((await paired.call('agents.list', {})).agents.length > 0);
  paired.close();
});

test('an unpaired connection is refused every call but device.redeem, in the same words, and stays open', async (t) => {
  const l = await listener(t);
  if (!l) return;
  const raw = await l.connect();
  const ask = (id: number, method: string, params: object) =>
    new Promise<{ error: { code: number; message: string; data: object } }>((ok) => {
      raw.onmessage = (m) => ok(JSON.parse(m));
      raw.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
    });
  const unpaired = { code: -32002, message: 'refused', data: { code: 'refused', gate: 'unpaired', reason: 'not paired' } };
  assert.deepEqual((await ask(1, 'hello', { client: 'c', protocol: 1 })).error, unpaired);
  assert.deepEqual((await ask(2, 'hello', { client: 'c', protocol: 1, token: 'hld_forged' })).error, unpaired);
  assert.deepEqual((await ask(3, 'agents.list', {})).error, unpaired);
  assert.deepEqual((await ask(4, 'no.such_method', {})).error, unpaired);
  raw.close();
});

test('a wrong, used or malformed redeem is refused', async (t) => {
  const l = await listener(t);
  if (!l) return;
  const code = l.daemon.issueCode();
  await redeemPairing(l.connect, code, 'phone');
  await assert.rejects(redeemPairing(l.connect, code, 'tablet'), (e: RpcError) => e.gate === 'pairing_code');
  await assert.rejects(redeemPairing(l.connect, 'NOPE', 'tablet'), (e: RpcError) => e.gate === 'pairing_code');
  await assert.rejects(
    redeemPairing(l.connect, l.daemon.issueCode(), ''),
    (e: RpcError) => e.code === 'bad_request' && e.data?.field === 'name',
  );
});

test('a redeem is never replayed by request_id: the same id and code is refused, not given the token again', async (t) => {
  const l = await listener(t);
  if (!l) return;
  const code = l.daemon.issueCode();
  const redeem = async () => {
    const raw = await l.connect();
    return new Promise<{ result?: { token: string }; error?: { data: { gate: string } } }>((ok) => {
      raw.onmessage = (m) => {
        ok(JSON.parse(m));
        raw.close();
      };
      raw.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'device.redeem', params: { code, name: 'phone', request_id: 'same' } }));
    });
  };
  assert.match((await redeem()).result!.token, /^hld_/);
  assert.equal((await redeem()).error?.data.gate, 'pairing_code');
});

test('a listener whose certificate is not the paired one, or a path other than /rpc, is not connected to', async (t) => {
  const l = await listener(t);
  if (!l) return;
  await assert.rejects(pinnedWebSocketTransport(listenerUrl(l.host), 'ab'.repeat(32)), /not the one that was paired/);
  await assert.rejects(pinnedWebSocketTransport(`wss://${l.host}/`, l.fingerprint), /did not accept|cannot connect|closed/);
  await assert.rejects(pinnedWebSocketTransport(`ws://${l.host}/rpc`, l.fingerprint), /wss/);
});
