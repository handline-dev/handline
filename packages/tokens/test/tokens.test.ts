import { test } from 'node:test';
import assert from 'node:assert/strict';
import { breakpointOf, breakpoints, colors, cssVariables } from '../src/index.ts';

test('widths fall in phone, tablet or PC at the documented edges', () => {
  assert.deepEqual([375, 639, 640, 768, 1023, 1024, 1280].map(breakpointOf), ['phone', 'phone', 'tablet', 'tablet', 'tablet', 'pc', 'pc']);
  assert.equal(breakpoints.phone, 640);
});

test('both schemes define every token, and the CSS variables name them in kebab case', () => {
  assert.deepEqual(Object.keys(colors.dark).sort(), Object.keys(colors.light).sort());
  assert.match(cssVariables('light'), /--bg: #fafaf9; --panel: #fff;/);
  assert.match(cssVariables('dark'), /--accent-ink: #0d1020;/);
  assert.match(cssVariables('light'), /--term-bg: #16150f;/);
});
