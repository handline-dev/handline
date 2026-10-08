import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Link, offerFrom, parseMode, pairRemote, plainRemoteHost, SettingsFile, type Open } from '../src/mode.ts';
import { trayMenu } from '../src/tray.ts';

const tmp = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'mode-')), 'settings.json');
const remote = { host: 'mac.tail1.ts.net:7443', fingerprint: null, token: 't0k', device: 'd1' };

test('the default mode is connect, a kept choice stays, and HANDLINE_MODE wins without being kept', () => {
  const f = new SettingsFile(tmp());
  assert.deepEqual(f.read(), { mode: 'connect', remote: null });
  f.write({ mode: 'own', remote });
  assert.equal(f.read().mode, 'own');
  assert.equal(f.read('connect').mode, 'connect');
  assert.equal(f.read().mode, 'own');
  assert.equal(parseMode('nonsense'), undefined);
});

test('the file holds a token, so only this user reads it', () => {
  const file = tmp();
  new SettingsFile(file).write({ mode: 'connect', remote });
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
});

test('a broken settings file gives the defaults', () => {
  const file = tmp();
  fs.writeFileSync(file, '{nope');
  assert.deepEqual(new SettingsFile(file).read(), { mode: 'connect', remote: null });
});

test('an address and a code make an offer; a plain address that is not Tailscale is refused', () => {
  assert.deepEqual(offerFrom({ address: 'mac.tail1.ts.net', code: 'abc' }), { host: 'mac.tail1.ts.net:7443', fingerprint: null, code: 'abc' });
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

const actions = { open() {}, startDaemon() {}, quit() {} };
const tray = (m: 'connect' | 'own', link: Partial<Link>) => {
  const calls: string[] = [];
  const items = trayMenu('stopped', '', actions, undefined, {
    mode: m,
    link: { status: 'connected', address: null, version: null, reason: '', ...link },
    switchTo: () => calls.push('switch'),
    pair: () => calls.push('pair'),
  });
  return { items, calls, labels: items.map((i) => i.label ?? '-') };
};

test('connect mode shows the daemon it talks to and never offers to start one', () => {
  const { labels } = tray('connect', { address: '127.0.0.1:7443', version: 'abc1234' });
  assert.ok(labels.includes('Mode: Connect to a daemon'));
  assert.ok(labels.includes('Daemon: 127.0.0.1:7443 · abc1234'));
  assert.ok(labels.includes('Connected'));
  assert.ok(!labels.includes('Start daemon'));
  assert.ok(labels.includes('Switch to Own daemon'));
});

test('connect mode with nothing running says so and offers pairing and the switch', () => {
  const { labels, items, calls } = tray('connect', { status: 'no-daemon' });
  assert.ok(labels.includes('Daemon: none'));
  assert.ok(labels.some((l) => /No daemon is running/.test(l)));
  items.find((i) => i.label === 'Pair with the link on the clipboard')!.click!();
  items.find((i) => i.label === 'Switch to Own daemon')!.click!();
  assert.deepEqual(calls, ['pair', 'switch']);
});

test('a pairing failure shows its reason', () => {
  assert.ok(tray('connect', { status: 'cannot-pair', reason: 'not a Handline gateway' }).labels.includes('Cannot pair: not a Handline gateway'));
});

test('own mode keeps the daemon status and Start, and switches back to connect', () => {
  const { labels } = tray('own', { address: '127.0.0.1:7443' });
  assert.ok(labels.includes('Mode: Own daemon'));
  assert.ok(labels.includes('Daemon: stopped'));
  assert.ok(labels.includes('Start daemon'));
  assert.ok(labels.includes('Switch to Connect'));
  assert.ok(!labels.includes('Pair with the link on the clipboard'));
});

test('the link remembers the version as the short sha', () => {
  const l = new Link();
  l.setVersion('0123456789abcdef');
  assert.equal(l.version, '0123456');
});
