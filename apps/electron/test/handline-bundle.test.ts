import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildPath, bundlePath, PLIST_NAMES } from '#src/handline-bundle.ts';

test('each Electron version has its own bundle, so a new version never replaces one that is running', () => {
  assert.equal(bundlePath('/d', '38.8.6'), '/d/Handline-38.8.6.app');
  assert.notEqual(bundlePath('/d', '38.8.6'), bundlePath('/d', '39.0.0'));
});

test('two first launches build in folders of their own', () => {
  const to = bundlePath('/d', '38.8.6');
  assert.notEqual(buildPath(to, 1), buildPath(to, 2));
  assert.notEqual(buildPath(to, 1), to);
});

test('the plist names say Handline, and the executable key matches the renamed file', () => {
  assert.deepEqual(Object.fromEntries(PLIST_NAMES), {
    CFBundleName: 'Handline',
    CFBundleDisplayName: 'Handline',
    CFBundleExecutable: 'Handline',
  });
});
