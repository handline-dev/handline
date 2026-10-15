import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findImageRefs, fitLongEdge, imageRef, insertImageRef } from '../src/images.ts';

test('the long edge shrinks to 2048 and keeps the shape', () => {
  assert.deepEqual(fitLongEdge(4096, 2048), { width: 2048, height: 1024 });
  assert.deepEqual(fitLongEdge(1000, 3000), { width: 683, height: 2048 });
  assert.equal(fitLongEdge(2048, 100), null);
});

test('an image is named with the attachment scheme and a clean caption', () => {
  assert.equal(imageRef('9f2c41d0a7b3e6c8', 'login [x]'), '![login  x](attachment:9f2c41d0a7b3e6c8)');
});

test('the line goes on its own at the cursor', () => {
  assert.equal(insertImageRef('abcdef', 3, 'R'), 'abc\nR\ndef');
  assert.equal(insertImageRef('abc', null, 'R'), 'abc\nR');
  assert.equal(insertImageRef('', 0, 'R'), 'R');
});

test('image references are found with their caption and id', () => {
  const t = 'a ![login](attachment:9f2c) b ![](attachment:ab_1)';
  assert.deepEqual(
    findImageRefs(t).map((r) => [t.slice(r.start, r.end), r.caption, r.id]),
    [
      ['![login](attachment:9f2c)', 'login', '9f2c'],
      ['![](attachment:ab_1)', '', 'ab_1'],
    ],
  );
  assert.deepEqual(findImageRefs('![x](http://a/b.png)'), []);
});
