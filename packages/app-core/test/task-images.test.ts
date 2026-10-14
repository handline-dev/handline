import { test } from 'node:test';
import assert from 'node:assert/strict';
import { imageRef, titleFromNote, withImages } from '../src/task-images.ts';

test('a reference has the contract form and a caption cannot break it', () => {
  assert.equal(imageRef('abc', 'the [button]'), '![the button](attachment:abc)');
});

test('a note and its images, one reference per line', () => {
  assert.equal(withImages('  Fix this ', ['a', 'b']), 'Fix this\n![capture](attachment:a)\n![capture](attachment:b)');
  assert.equal(withImages('', ['a']), '![capture](attachment:a)');
});

test('a title is the first line, cut at 60, or the fallback', () => {
  assert.equal(titleFromNote('Header overlaps\nmore', 'Capture'), 'Header overlaps');
  assert.equal(titleFromNote('', 'Capture'), 'Capture');
  assert.equal(titleFromNote('x'.repeat(80), 'c').length, 60);
});
