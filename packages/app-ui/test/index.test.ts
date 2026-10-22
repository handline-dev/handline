import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STATE_GLYPH, blockedBy, toneOf } from '#src/index.ts';

test('a state has a glyph and a tone, and an unknown state is idle', () => {
  assert.equal(STATE_GLYPH.doing, 'taskDoing');
  assert.equal(toneOf('blocked'), 'block');
  assert.equal(toneOf('answered'), 'idle');
});

test('waiting words', () => {
  assert.equal(blockedBy([3, 7]), 'blocked by T3, T7');
});

test('a popover flips to the side with room and to the edge that fits', async () => {
  const { placePopover } = await import('#src/index.ts');
  const view = { w: 400, h: 600 };
  const anchor = (top: number, left: number) => ({ top, bottom: top + 20, left, right: left + 40 });
  assert.deepEqual(placePopover(anchor(100, 10), { w: 200, h: 150 }, view), { side: 'bottom', align: 'start', maxHeight: 468 });
  assert.equal(placePopover(anchor(560, 10), { w: 200, h: 150 }, view).side, 'top');
  assert.equal(placePopover(anchor(560, 10), { w: 200, h: 150 }, view, { side: 'top' }).side, 'top');
  assert.equal(placePopover(anchor(20, 10), { w: 200, h: 150 }, view, { side: 'top' }).side, 'bottom');
  assert.equal(placePopover(anchor(100, 300), { w: 200, h: 100 }, view).align, 'end');
  assert.equal(placePopover(anchor(100, 10), { w: 200, h: 100 }, view, { align: 'end' }).align, 'start');
  assert.equal(placePopover(anchor(100, 300), { w: 200, h: 100 }, view, { align: 'end' }).align, 'end');
});
