import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rowPieces } from '../src/index.ts';

test('a row with no runs is its text; one with none past the text is cut at the run edges', () => {
  assert.deepEqual(rowPieces({ text: 'abc' }), [{ text: 'abc' }]);
  assert.deepEqual(rowPieces({ text: 'abcd', spans: [{ from: 1, to: 3, style: { fg: 1 } }] }), [
    { text: 'a' },
    { text: 'bc', style: { fg: 1 } },
    { text: 'd' },
  ]);
});

test('a run past the text is drawn at its column: default blanks first, then the run padded to its end', () => {
  assert.deepEqual(rowPieces({ text: 'abc', spans: [{ from: 8, to: 11, style: { bg: 1 } }] }), [
    { text: 'abc     ' },
    { text: '   ', style: { bg: 1 } },
  ]);
  assert.deepEqual(rowPieces({ text: '', spans: [{ from: 2, to: 3, style: { bg: 4 } }] }), [
    { text: '  ' },
    { text: ' ', style: { bg: 4 } },
  ]);
});

test('columns are code points, and overlapping or unsorted runs do not draw a cell twice', () => {
  assert.deepEqual(rowPieces({ text: '😀x', spans: [{ from: 0, to: 1, style: { bold: true } }] }), [
    { text: '😀', style: { bold: true } },
    { text: 'x' },
  ]);
  const p = rowPieces({
    text: 'abcdef',
    spans: [
      { from: 3, to: 5, style: { fg: 2 } },
      { from: 0, to: 4, style: { fg: 1 } },
    ],
  });
  assert.equal(p.map((x) => x.text).join(''), 'abcdef');
});
