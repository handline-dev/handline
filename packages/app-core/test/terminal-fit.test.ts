import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fitGrid, fontFor, heldByMe, heldByOther, sizeBreaks } from '../src/terminal-fit.ts';

test('fits at least the daemon minimum, in a smaller font on a narrow screen', () => {
  assert.deepEqual(fitGrid(396, 650), { cols: 80, rows: 66 });
  assert.equal(fontFor(396, 80), 8.2);
  assert.deepEqual(fitGrid(10, 10), { cols: 80, rows: 24 });
  assert.equal(fitGrid(9999, 9999).cols, 400);
  assert.equal(fontFor(9999, 20), 11);
  assert.equal(fontFor(300, 400), 6);
});

test('tells my hold from another device and from the daemon', () => {
  const pc = { kind: 'device', name: 'pc' } as never;
  const daemon = { kind: 'daemon', name: 'daemon' } as never;
  assert.ok(heldByOther(pc, 'phone') && !heldByMe(pc, 'phone'));
  assert.ok(heldByMe(pc, 'pc'));
  assert.ok(!heldByOther(daemon, 'phone') && !heldByOther(null, 'phone'));
});

test('a host wider than the screen breaks the view', () => {
  assert.ok(sizeBreaks(120, { cols: 60 }));
  assert.ok(!sizeBreaks(60, { cols: 60 }));
});
