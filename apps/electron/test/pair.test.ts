import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { memoryPair, type Transport } from '@handline/client';
import { ensureToken, founderKeyFile, NoFounderKey, pairSelf, phoneLink, stillPaired, TokenFile } from '../src/pair.ts';

/** A gateway's local socket: founder hello with the right key, device.pair, device.redeem, devices.list. */
function gateway(key: string) {
  const seen: string[] = [];
  const devices: string[] = [];
  let n = 0;
  const open = async (): Promise<Transport> => {
    const [client, server] = memoryPair();
    let founder = false;
    server.onmessage = (m) => {
      const { id, method, params } = JSON.parse(m);
      seen.push(method);
      const ok = (result: unknown) => server.send(JSON.stringify({ jsonrpc: '2.0', id, result }));
      const no = (reason: string) => server.send(JSON.stringify({ jsonrpc: '2.0', id, error: { message: 'refused', data: { reason } } }));
      if (method === 'hello') {
        founder = params.key === key;
        return founder ? ok({ role: 'founder' }) : no('bad key');
      }
      if (method === 'device.pair')
        return founder ? ok({ code: 'C0DE', uri: 'handline://pair?host=h&code=C0DE', expires_at: '2026-01-01T00:00:00Z' }) : no('role');
      if (method === 'device.redeem') {
        if (params.code !== 'C0DE') return no('pairing code');
        devices.push(`d${++n}`);
        return ok({ token: `tok${n}`, device: `d${n}` });
      }
      if (method === 'devices.list') return founder ? ok({ devices: devices.map((id) => ({ id })) }) : no('role');
      no('unknown');
    };
    return client;
  };
  return { open, seen, devices };
}

test('the key file is where the daemon keeps it, one folder per home', () => {
  assert.equal(founderKeyFile('/Users/a', '/Users/a/.handline'), '/Users/a/.config/orcapipe/protected/-Users-a--handline/founder-keys/key'); // local-ref-ok
});

test('pairSelf proves the key, asks for a code and trades it for a token; no key file is its own error', async () => {
  const g = gateway('K');
  const k = await pairSelf({ readKey: () => 'K', open: g.open, name: 'x' });
  assert.deepEqual(k, { token: 'tok1', device: 'd1' });
  assert.deepEqual(g.seen, ['hello', 'device.pair', 'device.redeem']);
  await assert.rejects(pairSelf({ readKey: () => null, open: g.open, name: 'x' }), NoFounderKey);
  await assert.rejects(pairSelf({ readKey: () => 'wrong', open: g.open, name: 'x' }), /bad key/);
});

test('a kept token is reused while the gateway lists its device, and replaced when it was revoked', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pair-'));
  const file = new TokenFile(path.join(dir, 'sub', 'device.json'));
  const g = gateway('K');
  const d = { readKey: () => 'K', open: g.open, name: 'x' };
  const wait = async () => undefined;
  assert.equal(await ensureToken(d, file, wait), 'tok1');
  assert.equal(await ensureToken(d, file, wait), 'tok1');
  assert.equal(g.devices.length, 1);
  assert.equal(fs.statSync(file['file']).mode & 0o777, 0o600);
  g.devices.length = 0; // revoked
  assert.equal(await stillPaired(d, 'd1'), false);
  assert.equal(await ensureToken(d, file, wait), 'tok2');
  assert.deepEqual(file.read(), { token: 'tok2', device: 'd2' });
});

test('it waits for the daemon to make its key, then pairs; with no key at all it gives up', async () => {
  const g = gateway('K');
  let reads = 0;
  const file = new TokenFile(path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'pair-')), 'device.json'));
  const d = { readKey: () => (++reads > 3 ? 'K' : null), open: g.open, name: 'x' };
  assert.equal(await ensureToken(d, file, async () => undefined), 'tok1');
  await assert.rejects(
    ensureToken({ ...d, readKey: () => null }, new TokenFile(path.join(os.tmpdir(), `none-${Date.now()}.json`)), async () => undefined, 3),
    NoFounderKey,
  );
});

test("phoneLink gives the gateway's link without redeeming its code", async () => {
  const g = gateway('K');
  const l = await phoneLink({ readKey: () => 'K', open: g.open, name: 'x' });
  assert.deepEqual(l, { uri: 'handline://pair?host=h&code=C0DE', expiresAt: '2026-01-01T00:00:00Z' });
  assert.deepEqual(g.seen, ['hello', 'device.pair']);
  await assert.rejects(phoneLink({ readKey: () => null, open: g.open, name: 'x' }), NoFounderKey);
});
