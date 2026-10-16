import assert from 'node:assert/strict';
import { test } from 'node:test';
import { pickUpdate, type Release } from '../src/update.ts';

const rel = (tag: string, code: number, prerelease: boolean, name = `handline-${tag}.apk`): Release => ({
  tag_name: tag,
  body: `notes\nversionCode: ${code}\n`,
  draft: false,
  prerelease,
  assets: [{ name, browser_download_url: `https://example.test/${tag}/${name}` }],
});

const all = [rel('nightly-20261016-9', 9, true), rel('v0.2.0', 7, false), rel('nightly-20261015-5', 5, true)];

test('nightly offers the newest prerelease above the installed build', () => {
  assert.equal(pickUpdate(all, 'nightly', 5)?.versionCode, 9);
  assert.equal(pickUpdate(all, 'nightly', 9), null);
});

test('stable ignores prereleases', () => {
  assert.equal(pickUpdate(all, 'stable', 5)?.versionCode, 7);
});

test('nightly to stable waits for a newer stable', () => {
  assert.equal(pickUpdate(all, 'stable', 9), null);
});

test('stable to nightly offers the newer nightly', () => {
  assert.equal(pickUpdate(all, 'nightly', 7)?.versionCode, 9);
});

test('drafts, releases without a number or without an apk are skipped', () => {
  const bad: Release[] = [
    { ...rel('a', 20, true), draft: true },
    { ...rel('b', 21, true), body: 'no number' },
    { ...rel('c', 22, true), assets: [] },
  ];
  assert.equal(pickUpdate(bad, 'nightly', 1), null);
});
