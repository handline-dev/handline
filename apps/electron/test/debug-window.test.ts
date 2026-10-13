import assert from 'node:assert/strict';
import test from 'node:test';
import { isDebugWindow } from '../src/debug-window.ts';

test('only the debug route on the page origin may open a window', () => {
  const origin = 'http://127.0.0.1:17443';
  assert.equal(isDebugWindow(`${origin}/#/debug/terminals`, origin), true);
  assert.equal(isDebugWindow(`${origin}/?ws=x#/debug/terminals`, origin), true);
  assert.equal(isDebugWindow(`${origin}/#/settings`, origin), false);
  assert.equal(isDebugWindow('http://127.0.0.1:9999/#/debug/terminals', origin), false);
  assert.equal(isDebugWindow('https://example.com/#/debug/terminals', origin), false);
  assert.equal(isDebugWindow('not a url', origin), false);
});
