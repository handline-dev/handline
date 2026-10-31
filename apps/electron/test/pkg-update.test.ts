import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { isNewer, parseLatest, SelfUpdater, updateUrl, type PkgPorts } from '#src/pkg-update.ts';
import { leftovers, STAGING, swapBundle } from '#src/swap.ts';
import { UpdateFlow } from '#src/update-flow.ts';

const YML = `version: 0.1.42
files:
  - url: Handline-0.1.42-universal-mac.zip
    sha512: AAA==
    size: 1
path: Handline-0.1.42-universal-mac.zip
sha512: 'ZZZ=='
releaseDate: '2026-10-31T13:44:34.790Z'
`;

test('latest-mac.yml: the version, the zip and its checksum from the top level', () => {
  assert.deepEqual(parseLatest(YML), { version: '0.1.42', path: 'Handline-0.1.42-universal-mac.zip', sha512: 'ZZZ==' });
  assert.equal(parseLatest('version: 1.0.0\n'), null);
});

test('app-update.yml names the address', () => {
  assert.equal(updateUrl('provider: generic\nurl: https://example.org/dl\nupdaterCacheDirName: x\n'), 'https://example.org/dl');
  assert.equal(updateUrl('provider: github\n'), null);
});

test('a later version is newer; a dev build is older than any release', () => {
  assert.ok(isNewer('0.1.43', '0.1.42'));
  assert.ok(isNewer('0.1.100', '0.1.99'));
  assert.ok(!isNewer('0.1.42', '0.1.42'));
  assert.ok(!isNewer('0.1.41', '0.1.42'));
  assert.ok(isNewer('0.1.42', '0.0.0-dev'));
  assert.ok(isNewer('0.0.0', '0.0.0-dev'));
});

function rig(o: { current?: string; idle?: boolean; sum?: string } = {}) {
  const calls: string[] = [];
  const flow = new UpdateFlow();
  const ports: PkgPorts = {
    current: () => o.current ?? '0.1.41',
    text: async (url) => (calls.push(`text ${url}`), YML),
    download: async (url, progress) => (calls.push(`download ${url}`), progress(0.5), '/tmp/x.zip'),
    sha512: async () => o.sum ?? 'ZZZ==',
    stage: async () => (calls.push('stage'), '/stage/Handline.app'),
    idle: async () => o.idle ?? true,
    swapAndRestart: async (s) => void calls.push(`swap ${s}`),
  };
  return { flow, calls, u: new SelfUpdater(ports, flow, 'https://example.org/dl/') };
}

test('a newer build is downloaded, checked and unpacked, then offered once; Restart now swaps it in', async () => {
  const r = rig();
  const phases: string[] = [];
  r.flow.onChange(() => phases.push(`${r.flow.view.phase}:${r.flow.view.step}`));
  await r.u.check();
  assert.deepEqual(r.calls, [
    'text https://example.org/dl/latest-mac.yml',
    'download https://example.org/dl/Handline-0.1.42-universal-mac.zip',
    'stage',
  ]);
  assert.equal(r.flow.view.phase, 'ready');
  assert.ok(phases.includes('preparing:Downloading') && phases.includes('preparing:Checking'));
  await r.u.check(); // told already: nothing more is fetched
  assert.equal(r.calls.length, 3);
  r.flow.act('restart');
  await new Promise((res) => setImmediate(res));
  assert.equal(r.calls.at(-1), 'swap /stage/Handline.app');
});

test('the same version, a busy app or a failed fetch offers nothing', async () => {
  const same = rig({ current: '0.1.42' });
  await same.u.check();
  assert.equal(same.flow.view.phase, 'idle');
  const busy = rig({ idle: false });
  await busy.u.check();
  assert.equal(busy.flow.view.phase, 'idle');
  assert.ok(!busy.calls.some((c) => c.startsWith('download')));
});

test('a download that does not match its checksum fails the update and says so; Try again prepares it again', async () => {
  const r = rig({ sum: 'WRONG' });
  await r.u.check();
  assert.equal(r.flow.view.phase, 'failed');
  assert.match(r.flow.view.reason, /checksum/);
  assert.ok(!r.calls.includes('stage'));
  r.flow.act('retry');
  await new Promise((res) => setImmediate(res));
  assert.equal(r.calls.filter((c) => c.startsWith('download')).length, 2);
});

test('the swap moves the old app aside and the new one into its place, and puts the old one back if that fails', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'swap-'));
  const installed = path.join(dir, 'Handline.app');
  const staged = path.join(dir, STAGING, 'Handline.app');
  fs.mkdirSync(installed);
  fs.writeFileSync(path.join(installed, 'v'), 'old');
  fs.mkdirSync(staged, { recursive: true });
  fs.writeFileSync(path.join(staged, 'v'), 'new');
  const aside = swapBundle(installed, staged);
  assert.equal(fs.readFileSync(path.join(installed, 'v'), 'utf8'), 'new');
  assert.equal(fs.readFileSync(path.join(aside, 'v'), 'utf8'), 'old');
  assert.deepEqual(leftovers(dir, fs.readdirSync(dir)).sort(), [aside, path.join(dir, STAGING)].sort());
  // A staged app that is not there: the old one stays.
  fs.rmSync(aside, { recursive: true });
  assert.throws(() => swapBundle(installed, path.join(dir, 'missing.app')));
  assert.equal(fs.readFileSync(path.join(installed, 'v'), 'utf8'), 'new');
  fs.rmSync(dir, { recursive: true, force: true });
});
