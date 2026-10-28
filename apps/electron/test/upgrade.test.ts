import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { mismatch, sameBuild } from '@handline/app-core';
import { readBuildSha } from '#src/build-id.ts';
import { upgradeDaemon, type UpgradeDeps } from '#src/upgrade.ts';

function rig(versions: (string | Error)[], upgrade: () => Promise<unknown> = async () => ({ ok: true })) {
  const order: string[] = [];
  let polled = 0;
  const d: UpgradeDeps = {
    build: async () => void order.push('build'),
    exe: '/app/handlined',
    sha: 'abc123def456',
    upgrade: async (a) => {
      order.push(`upgrade ${a.exe} ${a.sha}`);
      return upgrade();
    },
    version: async () => {
      const v = versions[Math.min(polled++, versions.length - 1)]!;
      if (v instanceof Error) throw v;
      return v;
    },
    wait: async () => void order.push('wait'),
    polls: 4,
  };
  return { d, order };
}

test('the daemon is built, told to upgrade, waited for until it reports the app build', async () => {
  const { d, order } = rig([new Error('down'), 'oldsha000000', 'abc123def456']);
  await upgradeDaemon(d);
  assert.deepEqual(order, ['build', 'upgrade /app/handlined abc123def456', 'wait', 'wait', 'wait']);
});

test('a refusal carries the daemon reason and nothing is waited for', async () => {
  const { d, order } = rig(['x'], async () => Promise.reject(new Error('exe prints another sha')));
  await assert.rejects(upgradeDaemon(d), /did not upgrade: exe prints another sha/);
  assert.ok(!order.includes('wait'));
});

test('a build that fails stops before the daemon is asked; one that never comes back is a failure', async () => {
  const a = rig(['x']);
  a.d.build = async () => Promise.reject(new Error('go build: syntax error'));
  await assert.rejects(upgradeDaemon(a.d), /syntax error/);
  assert.ok(!a.order.some((o) => o.startsWith('upgrade')));
  await assert.rejects(upgradeDaemon(rig(['oldsha000000']).d), /did not come back/);
});

test('a packaged app has no build step', async () => {
  const { d, order } = rig(['1.2.0+abc123def456']);
  delete d.build;
  await upgradeDaemon(d);
  assert.equal(order[0], 'upgrade /app/handlined abc123def456');
});

test('the daemon and the app are the same build by sha, however the daemon spells its version', () => {
  assert.ok(sameBuild('abc123def456', 'abc123def456'));
  assert.ok(sameBuild('1.2.0+abc123def456-dirty', 'abc123def456'));
  assert.ok(!sameBuild('0000000000aa', 'abc123def456'));
  assert.ok(sameBuild('unknown', 'abc123def456') && sameBuild(undefined, 'abc123def456') && sameBuild('abc', null));
});

test('the mismatch notice names both builds, short, and is null when they match', () => {
  assert.equal(mismatch('0000000aaaaa', 'abc123def456'), 'Daemon is 0000000, app is abc123d');
  assert.equal(mismatch('abc123def456', 'abc123def456'), null);
});

test('the build id is read from build.json, null when it is missing or damaged', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'build-'));
  try {
    assert.equal(readBuildSha(dir), null);
    fs.writeFileSync(path.join(dir, 'build.json'), '{"sha":"abc123def456"}');
    assert.equal(readBuildSha(dir), 'abc123def456');
    fs.writeFileSync(path.join(dir, 'build.json'), '{');
    assert.equal(readBuildSha(dir), null);
  } finally {
    fs.rmSync(dir, { recursive: true });
  }
});
