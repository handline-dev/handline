import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { AddressInfo } from 'node:net';
import { DaemonClient, RpcError, listenerUrl, parsePairingUri, redeemPairing } from '@handline/client';
import { pinnedWebSocketTransport } from '@handline/client-node';
import { FakeDaemon } from '../src/daemon.ts';
import { serveWs } from '../src/serve.ts';

/** The fake as the network listener: wss with a self-signed certificate, paired devices only. */
async function listener(t: { skip(why: string): void; after(fn: () => void): void }) {
  const dir = fs.mkdtempSync(path.join(process.env.TMPDIR ?? os.tmpdir(), 'pair-'));
  try { execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost', '-keyout', `${dir}/k.pem`, '-out', `${dir}/c.pem`], { stdio: 'ignore' }); }
  catch { t.skip('openssl is not available'); return null; }
  const cert = fs.readFileSync(`${dir}/c.pem`);
  const daemon = new FakeDaemon();
  const server = serveWs(daemon, 0, '127.0.0.1', { cert, key: fs.readFileSync(`${dir}/k.pem`) }, true);
  await new Promise((ok) => server.once('listening', ok));
  t.after(() => { daemon.stop(); server.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  const host = `127.0.0.1:${(server.address() as AddressInfo).port}`;
  const fingerprint = crypto.createHash('sha256').update(new crypto.X509Certificate(cert).raw).digest('hex');
  return { daemon, host, fingerprint, connect: () => pinnedWebSocketTransport(listenerUrl(host), fingerprint) };
}

test('a pairing uri names the listener, its certificate fingerprint and the code', () => {
  const o = parsePairingUri('handline://pair?host=10.0.0.2%3A7788&fp=AB12&code=K7Q2');
  assert.deepEqual(o, { host: '10.0.0.2:7788', fingerprint: 'ab12', code: 'K7Q2' });
  assert.throws(() => parsePairingUri('https://example.com/?code=1'), /not a handline pairing uri/);
});

test('a code is redeemed once for a token; with it hello makes the caller a member, without it nothing is served', async (t) => {
  const l = await listener(t); if (!l) return;
  const { token, device } = await redeemPairing(l.connect, l.daemon.issueCode(), 'phone');
  assert.equal(device, 'phone');

  const paired = new DaemonClient({ connect: l.connect, client: 'web', token, reconnect: false });
  const hello = await paired.start();
  assert.equal(hello.role, 'member'); assert.equal(hello.transport, 'network'); assert.deepEqual(hello.caller, { kind: 'device', name: 'phone' });
  assert.ok((await paired.call('agents.list', {})).agents.length > 0);
  paired.close();

  const anon = new DaemonClient({ connect: l.connect, client: 'web', reconnect: false });
  assert.equal((await anon.start()).role, 'anonymous');
  await assert.rejects(anon.call('agents.list', {}), (e: RpcError) => e.gate === 'role');
  anon.close();
});

test('a wrong, used or unknown code or token is refused', async (t) => {
  const l = await listener(t); if (!l) return;
  const code = l.daemon.issueCode();
  await redeemPairing(l.connect, code, 'phone');
  await assert.rejects(redeemPairing(l.connect, code, 'tablet'), (e: RpcError) => e.gate === 'pairing_code');
  await assert.rejects(redeemPairing(l.connect, 'NOPE', 'tablet'), (e: RpcError) => e.gate === 'pairing_code');
  const bad = new DaemonClient({ connect: l.connect, client: 'web', token: 'forged', reconnect: false });
  await assert.rejects(bad.start(), (e: RpcError) => e.gate === 'device_token');
  bad.close();
});

test('a listener whose certificate is not the paired one is not connected to', async (t) => {
  const l = await listener(t); if (!l) return;
  await assert.rejects(pinnedWebSocketTransport(listenerUrl(l.host), 'ab'.repeat(32)), /not the one that was paired/);
  await assert.rejects(pinnedWebSocketTransport(`ws://${l.host}`, l.fingerprint), /wss/);
});
