import { test } from 'node:test';
import assert from 'node:assert/strict';
import { STATE_GLYPH, blockedBy, toneOf } from '../src/index.ts';

test('a state has a glyph and a tone, and an unknown state is idle', () => {
  assert.equal(STATE_GLYPH.doing, 'taskDoing');
  assert.equal(toneOf('blocked'), 'block');
  assert.equal(toneOf('answered'), 'idle');
});

test('waiting words', () => {
  assert.equal(blockedBy([3, 7]), 'blocked by T3, T7');
});
