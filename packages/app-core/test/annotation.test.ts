import { test } from 'node:test';
import assert from 'node:assert/strict';
import { annotate, initialAnnotation, type AnnotationAction } from '../src/annotation.ts';
import { blankBitmap, exportAnnotated } from '../src/annotation-raster.ts';

const run = (...actions: AnnotationAction[]) => actions.reduce(annotate, initialAnnotation());
const px = (b: { width: number; data: Uint8ClampedArray }, x: number, y: number) => Array.from(b.data.slice((y * b.width + x) * 4, (y * b.width + x) * 4 + 4));

test('a pen stroke collects points and lands on end', () => {
  const s = run({ type: 'strokeStart', x: 1, y: 1 }, { type: 'strokeMove', x: 2, y: 2 }, { type: 'strokeEnd', x: 3, y: 3 });
  assert.equal(s.draft, null);
  assert.deepEqual(s.shapes[0]?.kind === 'pen' && s.shapes[0].points.length, 3);
});

test('a box tap with no drag leaves no shape, and text needs words', () => {
  const s = run({ type: 'tool', tool: 'box' }, { type: 'strokeStart', x: 5, y: 5 }, { type: 'strokeEnd', x: 5, y: 5 }, { type: 'text', x: 1, y: 1, text: '  ' });
  assert.equal(s.shapes.length, 0);
});

test('undo and redo walk the list; a new shape drops the redo', () => {
  let s = run({ type: 'text', x: 0, y: 0, text: 'a' }, { type: 'text', x: 0, y: 0, text: 'b' });
  s = annotate(annotate(s, { type: 'undo' }), { type: 'undo' });
  assert.equal(s.shapes.length, 0);
  assert.equal(s.redo.length, 2);
  s = annotate(s, { type: 'redo' });
  assert.equal(s.shapes.length, 1);
  assert.equal(annotate(s, { type: 'text', x: 0, y: 0, text: 'c' }).redo.length, 0);
});

test('the tool cannot change under a stroke', () => {
  const s = run({ type: 'strokeStart', x: 0, y: 0 }, { type: 'tool', tool: 'box' });
  assert.equal(s.tool, 'pen');
});

test('export paints the shapes and leaves the source alone', () => {
  const src = blankBitmap(20, 20);
  const s = run({ type: 'color', color: '#ff0000' }, { type: 'width', width: 4 }, { type: 'tool', tool: 'box' }, { type: 'strokeStart', x: 2, y: 2 }, { type: 'strokeEnd', x: 15, y: 15 });
  const out = exportAnnotated(src, s.shapes);
  assert.deepEqual(px(out, 2, 8), [255, 0, 0, 255]);
  assert.deepEqual(px(out, 8, 8), [0, 0, 0, 0]);
  assert.deepEqual(px(src, 2, 8), [0, 0, 0, 0]);
});

test('arrow and text draw something inside the bitmap', () => {
  const s = run({ type: 'tool', tool: 'arrow' }, { type: 'strokeStart', x: 2, y: 2 }, { type: 'strokeEnd', x: 40, y: 2 }, { type: 'text', x: 2, y: 10, text: 'Hi' });
  const out = exportAnnotated(blankBitmap(50, 40), s.shapes);
  assert.ok(out.data.some((v, i) => i % 4 === 3 && v === 255));
  assert.deepEqual(px(out, 20, 2), [229, 50, 45, 255]);
});
