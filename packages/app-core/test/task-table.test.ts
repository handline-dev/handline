import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Task } from '@handline/client';
import { LISTED, defaultFilter, durationMs, idleText, isOver, shown, sortTasks, taskTree, timeoutText } from '../src/task-table.ts';
import { taskAfter } from '../src/tasks.ts';

const NOW = Date.parse('2026-01-01T12:00:00Z');
const ago = (min: number) => new Date(NOW - min * 60_000).toISOString();
const task = (id: number, over: Partial<Task> = {}): Task => ({
  id,
  from: 'founder',
  to: 'a',
  title: `task ${id}`,
  state: 'queued',
  relation: 'manager',
  response: 'async',
  time_box: '2h',
  parent: null,
  created: ago(600),
  delivered: null,
  done: null,
  hold_reason: null,
  block_reason: null,
  evidence: null,
  ...over,
});
const ids = (l: { id: number }[]) => l.map((t) => t.id);

test('the default filter lists the open states without done and discarded, as the bridge does', () => {
  assert.ok(LISTED.includes('doing') && LISTED.includes('draft') && !LISTED.includes('done'));
  const all = [task(1), task(2, { state: 'done' }), task(3, { state: 'discarded' }), task(4, { state: 'doing' })];
  assert.deepEqual(ids(shown(all, defaultFilter())), [1, 4]);
  assert.deepEqual(ids(shown(all, { ...defaultFilter(), closed: true })), [1, 2, 3, 4]);
});

test('the agent filter keeps what the agent owns or asked for', () => {
  const all = [task(1, { to: 'x' }), task(2, { from: 'x', to: 'y' }), task(3, { to: 'z' })];
  assert.deepEqual(ids(shown(all, { ...defaultFilter(), agent: 'x' })), [1, 2]);
});

test('a state the person unticks is hidden, and closed tasks follow the closed switch alone', () => {
  const all = [task(1, { state: 'draft' }), task(2, { state: 'doing' }), task(3, { state: 'done' })];
  const states = new Set(['doing']);
  assert.deepEqual(ids(shown(all, { states, agent: '', closed: false })), [2]);
  assert.deepEqual(ids(shown(all, { states, agent: '', closed: true })), [2, 3]);
});

test('durations read as the daemon writes them', () => {
  assert.equal(durationMs('90m'), 90 * 60_000);
  assert.equal(durationMs('1h30m'), 90 * 60_000);
  assert.equal(durationMs('2d'), 2 * 86_400_000);
  assert.equal(durationMs('soon'), null);
  assert.equal(durationMs(null), null);
});

test('idle and timeout words: the box for a task not running, time left while it runs, over by past it', () => {
  assert.equal(idleText(task(1), NOW), '10h');
  assert.equal(idleText(task(1, { created: ago(5) }), NOW), '5m');
  assert.equal(idleText(task(1, { state: 'done' }), NOW), '');
  assert.equal(timeoutText(task(1), NOW), '2h');
  assert.equal(timeoutText(task(1, { state: 'doing', delivered: ago(30) }), NOW), '1h30m left');
  const late = task(1, { state: 'doing', delivered: ago(140) });
  assert.equal(timeoutText(late, NOW), 'over by 20m');
  assert.ok(isOver(late, NOW) && !isOver(task(1), NOW));
  assert.equal(timeoutText(task(1, { time_box: null }), NOW), '');
});

test('sort by priority puts 1 first and a task with none at the default 2; ties keep the number order', () => {
  const rows = [task(5, { priority: 3 }), task(2), task(9, { priority: 1 }), task(1)];
  assert.deepEqual(ids(sortTasks(rows, 'priority', false, NOW)), [9, 1, 2, 5]);
  assert.deepEqual(ids(sortTasks(rows, 'priority', true, NOW)), [5, 1, 2, 9]);
  assert.deepEqual(ids(sortTasks(rows, 'number', true, NOW)), [9, 5, 2, 1]);
  assert.deepEqual(ids(sortTasks([task(1, { title: 'b' }), task(2, { title: 'A' })], 'title', false, NOW)), [2, 1]);
});

test('the tree puts children under their parent, keeps a hidden parent as a dimmed row, and lifts a child whose parent is unknown', () => {
  const all = [task(1, { state: 'done' }), task(2, { parent: 1 }), task(3, { parent: 2 }), task(4), task(5, { parent: 99 })];
  const rows = shown(all, defaultFilter());
  const tree = taskTree(rows, all);
  assert.deepEqual(
    tree.map((r) => [r.task.id, r.depth, r.dim]),
    [
      [1, 0, true],
      [2, 1, false],
      [3, 2, false],
      [4, 0, false],
      [5, 0, false],
    ],
  );
});

test('editing the priority shows it at once', () => {
  assert.equal(taskAfter('edit', task(1), 'now', { priority: 1 })?.priority, 1);
  assert.equal(taskAfter('edit', task(1, { priority: 3 }), 'now', { title: 'x' })?.priority, 3);
});
