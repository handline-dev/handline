import { test } from 'node:test';
import assert from 'node:assert/strict';
import { defaultWorkspace, dropPreview, dropZone, groupsOf, openTab, splitNew, splitTab } from '#src/index.ts';

const pane = { w: 800, h: 600, strip: 32 };

test('the tab strip is its own zone, whatever the pointer does sideways', () => {
  assert.deepEqual(dropZone(pane, { x: 5, y: 10 }), { kind: 'strip' });
  assert.deepEqual(dropZone(pane, { x: 799, y: 31 }), { kind: 'strip' });
});

test('the body is the centre, except within 30% of a side, at least 40 px and at most 160 px', () => {
  assert.deepEqual(dropZone(pane, { x: 400, y: 300 }), { kind: 'center' });
  assert.deepEqual(dropZone(pane, { x: 100, y: 300 }), { kind: 'edge', edge: 'left' });
  assert.deepEqual(dropZone(pane, { x: 170, y: 300 }), { kind: 'center' }); // 800 wide: capped at 160
  assert.deepEqual(dropZone(pane, { x: 700, y: 300 }), { kind: 'edge', edge: 'right' });
  assert.deepEqual(dropZone(pane, { x: 400, y: 60 }), { kind: 'edge', edge: 'top' });
  assert.deepEqual(dropZone(pane, { x: 400, y: 590 }), { kind: 'edge', edge: 'bottom' });
  // a small pane still keeps 40 px edges
  assert.deepEqual(dropZone({ w: 100, h: 100, strip: 10 }, { x: 35, y: 60 }), { kind: 'edge', edge: 'left' });
});

test('a corner goes to the side it is closer to by share, so no corner is empty', () => {
  assert.deepEqual(dropZone(pane, { x: 20, y: 200 }), { kind: 'edge', edge: 'left' });
  assert.deepEqual(dropZone(pane, { x: 150, y: 40 }), { kind: 'edge', edge: 'top' });
});

test('without edges only the strip and the centre are left', () => {
  assert.deepEqual(dropZone(pane, { x: 1, y: 300 }, false), { kind: 'center' });
  assert.deepEqual(dropZone(pane, { x: 1, y: 1 }, false), { kind: 'strip' });
});

test('the preview is the exact box the split takes: half of the pane, the strip included', () => {
  assert.deepEqual(dropPreview(pane, { kind: 'edge', edge: 'right' }), { x: 400, y: 0, w: 400, h: 600 });
  assert.deepEqual(dropPreview(pane, { kind: 'edge', edge: 'left' }), { x: 0, y: 0, w: 400, h: 600 });
  assert.deepEqual(dropPreview(pane, { kind: 'edge', edge: 'bottom' }), { x: 0, y: 300, w: 800, h: 300 });
  assert.deepEqual(dropPreview(pane, { kind: 'edge', edge: 'top' }), { x: 0, y: 0, w: 800, h: 300 });
  assert.deepEqual(dropPreview(pane, { kind: 'center' }), { x: 0, y: 0, w: 800, h: 600 });
  assert.equal(dropPreview(pane, { kind: 'strip' }), null);
  // and the split really is half
  const ws = splitTab(openTab(defaultWorkspace(), 'markdown', { path: 'a' }), 'markdown:a', 'g1', 'right')!;
  assert.equal(ws.root.type === 'split' && ws.root.at, 0.5);
  assert.equal(groupsOf(ws.root).length, 2);
});

test('a task dragged out of a list opens as its own pane on the edge it lands on, and is not opened twice', () => {
  const ws = defaultWorkspace();
  const split = splitNew(ws, 'tasks', { task: 7, pinned: true }, 'g1', 'right')!;
  assert.equal(groupsOf(split.root).length, 2);
  assert.ok(split.tabs['tasks:7']);
  assert.equal(groupsOf(split.root).find((g) => g.tabs.includes('tasks:7'))?.id, split.active);
  assert.equal(splitNew(split, 'tasks', { task: 7, pinned: true }, 'g1', 'left')!.tabs['tasks:7'], split.tabs['tasks:7']);
});
