import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classifyGesture, initialMarks, keptMarks, markData, markText, marksReducer, marksToShapes, type MarksAction } from '../src/marks.ts';
import { blankBitmap, exportAnnotated } from '../src/annotation-raster.ts';

const run = (...actions: MarksAction[]) => actions.reduce(marksReducer, initialMarks());
const gesture = (...ps: [number, number][]): MarksAction[] => [
  { type: 'down', x: ps[0]![0], y: ps[0]![1] },
  ...ps.slice(1, -1).map(([x, y]): MarksAction => ({ type: 'move', x, y })),
  { type: 'up', x: ps.at(-1)![0], y: ps.at(-1)![1] },
];
const circle = (cx: number, cy: number, r: number, n = 24): [number, number][] =>
  Array.from({ length: n + 1 }, (_, i) => [cx + r * Math.cos((2 * Math.PI * i) / n), cy + r * Math.sin((2 * Math.PI * i) / n)]);

test('a tap pins a circle round the point, a drag draws a box, a loop marks an area', () => {
  const tap = classifyGesture([{ x: 100, y: 50 }], 8, 16);
  assert.deepEqual([tap.kind, tap.rect], ['pin', { x: 84, y: 34, w: 32, h: 32 }]);
  const small = classifyGesture([{ x: 100, y: 50 }, { x: 103, y: 52 }], 8, 16);
  assert.equal(small.kind, 'pin'); // inside the slop: a tap with a shaky finger
  const box = classifyGesture([{ x: 200, y: 120 }, { x: 150, y: 90 }, { x: 40, y: 20 }], 8, 16);
  assert.deepEqual([box.kind, box.rect], ['box', { x: 40, y: 20, w: 160, h: 100 }]); // dragged up and left: still a normal rect
  const loop = classifyGesture(circle(100, 100, 40).map(([x, y]) => ({ x, y })), 8, 16);
  assert.equal(loop.kind, 'area');
  assert.deepEqual(loop.rect, { x: 60, y: 60, w: 80, h: 80 });
  assert.ok(loop.points!.length > 3 && loop.points!.length <= 64);
});

test('a drag along an edge is a box with some height, and a drag out and back is not a loop', () => {
  const line = classifyGesture([{ x: 10, y: 10 }, { x: 110, y: 10 }], 8, 16);
  assert.deepEqual([line.kind, line.rect.h], ['box', 8]);
  const back = classifyGesture([{ x: 10, y: 10 }, { x: 110, y: 12 }, { x: 12, y: 11 }], 8, 16);
  assert.equal(back.kind, 'area'); // the path went out and came back: what is between is marked
});

test('a gesture adds a mark with empty words and opens its memo; the numbers count the marks in order', () => {
  const s = run(...gesture([10, 10], [10, 10]), ...gesture([50, 50], [120, 90]));
  assert.deepEqual(s.marks.map((m) => [m.id, m.kind, m.text]), [[1, 'pin', ''], [2, 'box', '']]);
  assert.equal(s.open, 2);
  assert.equal(s.draft, null);
});

test('memo words are set on the mark that is open; an empty memo is dropped, with the numbers closing up', () => {
  let s = run(...gesture([10, 10], [10, 10]), { type: 'memo', id: 1, text: 'Tab label is cut off' });
  s = [...gesture([50, 50], [120, 90]), { type: 'memo', id: 2, text: '   ' } as MarksAction].reduce(marksReducer, s);
  s = [...gesture([5, 80], [30, 99]), { type: 'memo', id: 3, text: 'too small' } as MarksAction].reduce(marksReducer, s);
  assert.equal(s.marks.length, 3);
  assert.deepEqual(keptMarks(s.marks).map((m) => m.id), [1, 3]);
  assert.deepEqual(markData(s.marks).map((d) => [d.n, d.text]), [[1, 'Tab label is cut off'], [2, 'too small']]);
});

test('Undo takes back the last mark, and always steps back one gesture; it does nothing with none', () => {
  let s = run(...gesture([10, 10], [10, 10]), ...gesture([50, 50], [120, 90]));
  s = marksReducer(s, { type: 'undo' });
  assert.deepEqual(s.marks.map((m) => m.id), [1]);
  assert.equal(s.open, null);
  s = marksReducer(marksReducer(s, { type: 'undo' }), { type: 'undo' });
  assert.equal(s.marks.length, 0);
  // a removal is a step too
  s = run(...gesture([10, 10], [10, 10]), { type: 'remove', id: 1 });
  assert.equal(s.marks.length, 0);
  assert.equal(marksReducer(s, { type: 'undo' }).marks.length, 1);
});

test('a cancelled gesture leaves nothing, and the sizes follow the picture', () => {
  const s = run({ type: 'scale', slop: 20, radius: 40 }, { type: 'down', x: 5, y: 5 }, { type: 'move', x: 90, y: 90 }, { type: 'cancel' });
  assert.deepEqual([s.draft, s.marks.length], [null, 0]);
  const pin = run({ type: 'scale', slop: 20, radius: 40 }, ...gesture([100, 100], [110, 105]));
  assert.deepEqual([pin.marks[0]!.kind, pin.marks[0]!.rect.w], ['pin', 80]);
});

test('the message is the note, the numbered lines and each kept mark as data; with no kept mark it is the note alone', () => {
  const s = run(
    ...gesture([10, 10], [10, 10]),
    { type: 'memo', id: 1, text: 'Tab label\nis cut off' },
    ...gesture([50, 50], [120, 90]),
    { type: 'memo', id: 2, text: '' },
    ...gesture(...circle(100, 100, 40)),
    { type: 'memo', id: 3, text: 'This text is too small' },
  );
  const text = markText('Two things', s.marks);
  const [note, one, two, fence, json] = text.split('\n');
  assert.deepEqual([note, one, two, fence], ['Two things', '1. Tab label is cut off', '2. This text is too small', '```marks']);
  assert.deepEqual(JSON.parse(json!), [
    { n: 1, shape: 'pin', x: -6, y: -6, w: 32, h: 32, text: 'Tab label is cut off' },
    { n: 2, shape: 'area', x: 60, y: 60, w: 80, h: 80, text: 'This text is too small' },
  ]);
  assert.equal(markText(' just a note ', run(...gesture([1, 1], [1, 1])).marks), 'just a note');
});

test('the marked picture draws only the kept marks, in red, and a ring leaves its inside alone', () => {
  const s = run(...gesture([40, 40], [40, 40]), { type: 'memo', id: 1, text: 'here' }, ...gesture([5, 5], [20, 20]));
  const shapes = marksToShapes(s.marks, 2);
  assert.equal(shapes.filter((x) => x.kind === 'text').length, 1); // one number: the empty memo's mark is left out
  const out = exportAnnotated(blankBitmap(100, 100), shapes);
  const px = (x: number, y: number) => Array.from(out.data.slice((y * 100 + x) * 4, (y * 100 + x) * 4 + 4));
  assert.deepEqual(px(24, 40), [229, 50, 45, 255]);
  assert.deepEqual(px(40, 40), [0, 0, 0, 0]);
  assert.deepEqual(px(10, 10), [0, 0, 0, 0]); // the dropped mark's box is not drawn
});
