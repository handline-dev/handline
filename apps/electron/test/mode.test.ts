import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { helloOf, Link, offerFrom, pairRemote, plainRemoteHost, SettingsFile, type Open } from '../src/mode.ts';
import { trayMenu } from '../src/tray.ts';

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'mode-')), 'settings.json');
const remote = { host: 'mac.tail1.ts.net:7443', fingerprint: null, token: 't0k', device: 'd1' };

test('the default is this Mac, a kept pairing stays, and a mode an older file holds is ignored', () => {
  const file = tmp();
  const f = new SettingsFile(file);
  assert.deepEqual(f.read(), { remote: null });
  f.write({ remote });
  assert.deepEqual(f.read(), { remote });
  fs.writeFileSync(file, JSON.stringify({ mode: 'own', remote: null }));
  assert.deepEqual(f.read(), { remote: null });
});

test('the file holds a token, so only this user reads it', () => {
  const file = tmp();
  new SettingsFile(file).write({ remote });
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
});

test('a broken settings file gives the defaults', () => {
  const file = tmp();
  fs.writeFileSync(file, '{nope');
  assert.deepEqual(new SettingsFile(file).read(), { remote: null });
});

test('an address and a code make an offer; a plain address that is not Tailscale is refused', () => {
  assert.deepEqual(offerFrom({ address: 'mac.tail1.ts.net', code: 'abc' }), {
    host: 'mac.tail1.ts.net:7443',
    fingerprint: null,
    code: 'abc',
  });
  assert.throws(() => offerFrom({ address: '192.168.1.5:7443', code: 'abc' }), /Tailscale/);
  assert.throws(() => offerFrom({}), /pairing link/);
});

test('a plain Tailscale remote is the one host the page may reach over ws', () => {
  assert.equal(plainRemoteHost(remote), 'mac.tail1.ts.net');
  assert.equal(plainRemoteHost({ ...remote, fingerprint: 'ab' }), null);
});

const never: Open = () => new Promise(() => undefined);

test('a gateway that never answers fails with a sentence instead of hanging', async () => {
  await assert.rejects(
    pairRemote({ address: 'mac.tail1.ts.net', code: 'abc' }, { open: never, name: 'x', timeoutMs: 20 }),
    /mac\.tail1\.ts\.net:7443 cannot be paired with: .*nothing answered/,
  );
});

test('a refused connection says why', async () => {
  const open: Open = () => Promise.reject(new Error('cannot connect: ECONNREFUSED'));
  await assert.rejects(pairRemote({ address: 'mac.tail1.ts.net', code: 'abc' }, { open, name: 'x' }), /ECONNREFUSED/);
});

const actions = { open() {}, startDaemon() {}, stopDaemon() {}, quit() {} };
const tray = (state: Parameters<typeof trayMenu>[0], link: Partial<Link>, remote = false) => {
  const calls: string[] = [];
  const items = trayMenu(state, '', actions, undefined, {
    link: { status: 'connected', address: null, version: null, reason: '', ...link },
    pair: () => calls.push('pair'),
    useLocal: remote ? () => calls.push('local') : undefined,
  });
  return { items, calls, labels: items.map((i) => i.label ?? '-') };
};

test('the tray shows the daemon it talks to, pairs from the clipboard and offers Stop only while it runs', () => {
  const { labels, items, calls } = tray('running', { address: '127.0.0.1:7443', version: 'abc1234' });
  assert.ok(labels.includes('Daemon: 127.0.0.1:7443 · abc1234'));
  assert.ok(labels.includes('Daemon: running'));
  assert.ok(labels.includes('Stop daemon…'));
  assert.ok(!labels.some((l) => /mode/i.test(l)));
  items.find((i) => i.label === 'Pair with the link on the clipboard')!.click!();
  assert.deepEqual(calls, ['pair']);
  assert.ok(!tray('stopped', {}).labels.includes('Stop daemon…'));
  assert.ok(tray('stopped', {}).labels.includes('Start daemon'));
});

test('quit is the last item and is not the stop item', () => {
  const stops: string[] = [];
  const items = trayMenu('running', '', { ...actions, stopDaemon: () => stops.push('stop'), quit: () => stops.push('quit') });
  assert.equal(items.at(-1)!.label, 'Quit Handline');
  items.at(-1)!.click!();
  assert.deepEqual(stops, ['quit']);
});

test('paired with another device, the tray goes back to this Mac and offers no Stop', () => {
  const { labels, items, calls } = tray('running', { address: 'mac.ts.net:7443' }, true);
  assert.ok(!labels.includes('Stop daemon…'));
  items.find((i) => i.label === "Use this Mac's daemon")!.click!();
  assert.deepEqual(calls, ['local']);
});

test('a pairing failure shows its reason', () => {
  assert.ok(
    tray('running', { status: 'cannot-pair', reason: 'not a Handline gateway' }, true).labels.includes(
      'Cannot pair: not a Handline gateway',
    ),
  );
});
test('the link remembers the version as the short sha', () => {
  const l = new Link();
  l.setVersion('0123456789abcdef');
  assert.equal(l.version, '0123456');
});

const answering =
  (reply: unknown): Open =>
  async () => {
    const t = {
      onmessage: null as ((m: string) => void) | null,
      onclose: null as (() => void) | null,
      send() {
        setTimeout(() => t.onmessage?.(JSON.stringify(reply)), 0);
      },
      close() {},
    };
    return t as never;
  };

test('connected means a hello with our token was accepted, and the version comes from it', async () => {
  assert.equal(await helloOf(remote, answering({ jsonrpc: '2.0', id: 1, result: { daemon: 'abc1234def' } })), 'abc1234def');
  await assert.rejects(
    helloOf(remote, answering({ jsonrpc: '2.0', id: 1, error: { message: 'refused', data: { reason: 'this device is not paired' } } })),
    /this device is not paired/,
  );
});
