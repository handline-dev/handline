import { test } from 'node:test';
import assert from 'node:assert/strict';
import { blockedBy, glyphOf, hostFull, toneOf } from '../src/index.ts';

test('a state has a glyph and a tone, and an unknown state falls back', () => {
  assert.equal(glyphOf('doing'), 'taskDoing');
  assert.equal(toneOf('blocked'), 'block');
  assert.equal(glyphOf('reported'), 'taskPending');
  assert.equal(toneOf('reported'), 'idle');
});

test('waiting words', () => {
  assert.equal(blockedBy([3, 7]), 'blocked by T3, T7');
  assert.equal(hostFull(4, 4), 'host full: 4 of 4');
});
