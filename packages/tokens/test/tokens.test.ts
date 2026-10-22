import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ansi, breakpointOf, breakpoints, colors, scheme, space, tokensCss, typeScale, typeSteps } from '#src/index.ts';

test('widths fall in phone, tablet or PC at the documented edges', () => {
  assert.deepEqual([375, 639, 640, 768, 1023, 1024, 1280].map(breakpointOf), ['phone', 'phone', 'tablet', 'tablet', 'tablet', 'pc', 'pc']);
  assert.equal(breakpoints.phone, 640);
});

test('both schemes define every token, and the mobile palette is read from them', () => {
  assert.deepEqual(Object.keys(colors.dark).sort(), Object.keys(colors.light).sort());
  assert.equal(colors.dark.bg, scheme.dark.surface[0]);
  assert.equal(colors.light.accent, scheme.light.accent);
});

test('four surfaces, six type steps on both scales, space on a 4 px grid, 16 terminal colours', () => {
  assert.equal(scheme.dark.surface.length, 4);
  assert.deepEqual(typeSteps.length, 6);
  for (const s of typeSteps) assert.ok(typeScale.pc[s].size > 0 && typeScale.phone[s].size > 0);
  for (const v of Object.values(space)) assert.equal(v % 4, 0);
  assert.equal(ansi.length, 16);
});

test('the PC tokens.css is the generated one', () => {
  const file = readFileSync(new URL('../../../apps/web/src/tokens.css', import.meta.url), 'utf8');
  assert.equal(file, tokensCss(), 'run `pnpm --filter @handline/tokens build:css` and prettier');
});
