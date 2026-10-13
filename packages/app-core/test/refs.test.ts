import { test } from 'node:test';
import assert from 'node:assert/strict';
import { addTab, defaultWorkspace, findRefs, openTaskTab, taskRef } from '../src/index.ts';

const found = (s: string, o?: Parameters<typeof findRefs>[1]) => findRefs(s, o).map((r) => [s.slice(r.start, r.end), r.kind, r.id]);

test('a task number is found in running text, at the edges and beside punctuation', () => {
  assert.deepEqual(found('T3610 is done; see (T12), T7.'), [
    ['T3610', 'task', 3610],
    ['T12', 'task', 12],
    ['T7', 'task', 7],
  ]);
});

test('what is not a task number is left alone', () => {
  assert.deepEqual(found('MAT3 T12abc t12 T 5 T0 a/T5 x.T5 T1234567890 &#38;'), []);
});

test('a message number counts only where messages are asked for, and never inside an entity', () => {
  assert.deepEqual(found('see #42 and T9 &#38;'), [['T9', 'task', 9]]);
  assert.deepEqual(found('see #42 and T9 &#38;', { messages: true }), [
    ['#42', 'message', 42],
    ['T9', 'task', 9],
  ]);
});

test('an unknown task id is dropped when the caller says which are known', () => {
  assert.deepEqual(found('T1 T99', { task: (id) => id <= 50 }), [['T1', 'task', 1]]);
});

test('a ref reads back as taskRef writes it', () => {
  assert.equal(findRefs(taskRef(3610))[0]!.id, 3610);
});

test('openTaskTab opens a tab per task and finds an open one', () => {
  const a = openTaskTab(defaultWorkspace(), 5);
  const b = openTaskTab(a, 6);
  assert.deepEqual(
    Object.keys(b.tabs).filter((t) => t.startsWith('tasks:')),
    ['tasks:5', 'tasks:6'],
  );
  assert.equal(Object.keys(openTaskTab(b, 5).tabs).length, Object.keys(b.tabs).length);
  assert.equal(addTab(defaultWorkspace(), 'tasks').tabs['tasks']?.kind, 'tasks'); // the Tasks tab keeps its id
});
