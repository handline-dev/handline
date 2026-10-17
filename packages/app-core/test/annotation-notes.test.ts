import { test } from 'node:test';
import assert from 'node:assert/strict';
import { annotate, initialAnnotation, notesOf, shapesToSvg, type AnnotationAction } from '../src/annotation.ts';
import { blankBitmap, exportAnnotated } from '../src/annotation-raster.ts';

const run = (...actions: AnnotationAction[]) => actions.reduce(annotate, initialAnnotation());
const px = (b: { width: number; data: Uint8ClampedArray }, x: number, y: number) =>
  Array.from(b.data.slice((y * b.width + x) * 4, (y * b.width + x) * 4 + 4));

test('a note needs words, and its words can change or be taken away', () => {
  const at = { x: 30, y: 30 };
  let s = run({ type: 'note', from: at, to: at, text: '  ' });
  assert.equal(s.shapes.length, 0);
  s = run({ type: 'note', from: at, to: at, text: 'wrong colour' });
  const id = s.shapes[0]!.id;
  s = annotate(s, { type: 'noteText', id, text: 'too dark' });
  assert.equal(notesOf(s.shapes)[0]?.text, 'too dark');
  assert.equal(annotate(s, { type: 'noteText', id, text: '' }).shapes.length, 0);
});

test('a note draws its number in the preview, counted among the notes only', () => {
  const s = run(
    { type: 'text', x: 0, y: 0, text: 'hi' },
    { type: 'note', from: { x: 10, y: 10 }, to: { x: 10, y: 10 }, text: 'a' },
    { type: 'note', from: { x: 50, y: 50 }, to: { x: 90, y: 80 }, text: 'b' },
  );
  const numbers = shapesToSvg(s.shapes, 100, 100).elements.flatMap((e) => (e.type === 'text' && e.text !== 'HI' ? [e.text] : []));
  assert.deepEqual(numbers, ['1', '2']);
});

test('export outlines a region note and leaves its inside alone', () => {
  const s = run(
    { type: 'color', color: '#ff0000' },
    { type: 'width', width: 2 },
    { type: 'note', from: { x: 10, y: 10 }, to: { x: 60, y: 60 }, text: 'here' },
  );
  const out = exportAnnotated(blankBitmap(80, 80), s.shapes);
  assert.deepEqual(px(out, 10, 30), [255, 0, 0, 255]);
  assert.deepEqual(px(out, 40, 40), [0, 0, 0, 0]);
});

test('undo reverts a note edit and a note removal, redo puts them back', () => {
  const at = { x: 30, y: 30 };
  let s = run({ type: 'note', from: at, to: at, text: 'wrong colour' });
  const id = s.shapes[0]!.id;
  s = annotate(s, { type: 'noteText', id, text: 'too dark' });
  s = annotate(s, { type: 'undo' });
  assert.equal(notesOf(s.shapes)[0]?.text, 'wrong colour');
  s = annotate(s, { type: 'redo' });
  assert.equal(notesOf(s.shapes)[0]?.text, 'too dark');
  s = annotate(annotate(s, { type: 'noteText', id, text: '' }), { type: 'undo' });
  assert.equal(notesOf(s.shapes)[0]?.text, 'too dark');
});
