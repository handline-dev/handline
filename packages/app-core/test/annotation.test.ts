import { test } from 'node:test';
import assert from 'node:assert/strict';
import { annotate, shapesToSvg, initialAnnotation, type AnnotationAction } from '../src/annotation.ts';
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

test('scale shrinks positions and widths with the bitmap', () => {
  const s = run({ type: 'color', color: '#ff0000' }, { type: 'width', width: 8 }, { type: 'tool', tool: 'box' }, { type: 'strokeStart', x: 20, y: 20 }, { type: 'strokeEnd', x: 150, y: 150 });
  const out = exportAnnotated(blankBitmap(100, 100), s.shapes, 0.5);
  assert.deepEqual(px(out, 10, 50), [255, 0, 0, 255]);
  assert.deepEqual(px(out, 50, 50), [0, 0, 0, 0]);
  assert.deepEqual(px(out, 10, 90), [0, 0, 0, 0]);
});

test('svg data has a path per shape and keeps the model units', () => {
  const s = run({ type: 'strokeStart', x: 1, y: 2 }, { type: 'strokeEnd', x: 3, y: 4 }, { type: 'tool', tool: 'box' }, { type: 'strokeStart', x: 0, y: 0 }, { type: 'strokeEnd', x: 10, y: 5 }, { type: 'tool', tool: 'arrow' }, { type: 'strokeStart', x: 0, y: 0 }, { type: 'strokeEnd', x: 100, y: 0 }, { type: 'text', x: 5, y: 6, text: 'hi' });
  const svg = shapesToSvg(s.shapes, 640, 480);
  assert.equal(svg.viewBox, '0 0 640 480');
  assert.equal(svg.elements.length, 4);
  assert.deepEqual(svg.elements[0], { type: 'path', d: 'M1 2L3 4', stroke: '#e5322d', strokeWidth: 6 });
  assert.deepEqual(svg.elements[1]?.type === 'path' && svg.elements[1].d, 'M0 0L10 0L10 5L0 5Z');
  assert.equal(svg.elements[2]?.type === 'path' && svg.elements[2].d.startsWith('M0 0L100 0M100 0L'), true);
  assert.deepEqual(svg.elements[3], { type: 'text', x: 5, y: 6, fontSize: 34.29, fill: '#e5322d', text: 'HI' });
});

test('a stroke of a billion pixels is drawn only where it is visible, and fast', () => {
  const s = run({ type: 'color', color: '#ff0000' }, { type: 'strokeStart', x: -1e9, y: 5 }, { type: 'strokeEnd', x: 1e9, y: 5 });
  const t = Date.now();
  const out = exportAnnotated(blankBitmap(30, 10), s.shapes);
  assert.ok(Date.now() - t < 1000);
  assert.deepEqual(px(out, 15, 5), [255, 0, 0, 255]);
  const off = exportAnnotated(blankBitmap(30, 10), run({ type: 'strokeStart', x: -1e9, y: 500 }, { type: 'strokeEnd', x: 1e9, y: 500 }).shapes);
  assert.ok(off.data.every((v) => v === 0));
});

test('a circle is drawn as an outline inside its box: the rim is painted, the middle and the corner are not', () => {
  const s = run({ type: 'color', color: '#ff0000' }, { type: 'width', width: 4 }, { type: 'tool', tool: 'ellipse' }, { type: 'strokeStart', x: 10, y: 10 }, { type: 'strokeEnd', x: 90, y: 90 });
  const out = exportAnnotated(blankBitmap(100, 100), s.shapes);
  assert.deepEqual(px(out, 10, 50), [255, 0, 0, 255]);
  assert.deepEqual(px(out, 50, 10), [255, 0, 0, 255]);
  assert.deepEqual(px(out, 50, 50), [0, 0, 0, 0]);
  assert.deepEqual(px(out, 12, 12), [0, 0, 0, 0]);
  assert.equal(shapesToSvg(s.shapes, 100, 100).elements[0]?.type === 'path' && shapesToSvg(s.shapes, 100, 100).elements[0].d.endsWith('Z'), true);
});
