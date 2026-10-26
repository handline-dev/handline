import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bundleAt } from '#src/bundle.ts';

test('a packaged app finds its web build, daemon and CLI under Resources; a checkout has no bundle', () => {
  assert.deepEqual(bundleAt(true, '/Applications/Handline.app/Contents/Resources'), {
    web: '/Applications/Handline.app/Contents/Resources/web',
    handlined: '/Applications/Handline.app/Contents/Resources/bin/handlined',
    hl: '/Applications/Handline.app/Contents/Resources/bin/hl',
  });
  assert.equal(bundleAt(false, '/x'), null);
});
