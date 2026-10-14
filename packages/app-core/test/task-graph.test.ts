import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Task } from '@handline/client';
import { taskGraph, waitReason } from '../src/task-graph.ts';

const task = (id: number, over: Partial<Task> = {}): Task => ({
  id,
  from: 'human',
  to: 'a',
  title: `task ${id}`,
  state: 'queued',
  relation: 'manager',
  response: 'async',
  time_box: null,
  parent: null,
  created: '2026-01-01T00:00:00Z',
  delivered: null,
  done: null,
  hold_reason: null,
  block_reason: null,
  evidence: null,
  ...over,
});
const at = (g: ReturnType<typeof taskGraph>, id: number) => g.nodes.find((n) => n.id === id)!;

test('a holder sits above the task that waits on it, and the edge says why', () => {
  const g = taskGraph([task(1, { state: 'doing' }), task(2, { blocked_by: [1], state: 'blocked' })], { me: 'a' });
  assert.ok(at(g, 1).y < at(g, 2).y);
  assert.deepEqual(g.edges.map((e) => [e.from, e.to, e.label]), [[1, 2, 'blocked by T1']]);
  assert.ok(g.edges[0]!.points.length >= 2);
  assert.equal(at(g, 2).reason, 'blocked by T1');
  assert.equal(at(g, 1).reason, null);
});

test('closed tasks are out unless asked for, and a closed holder no longer holds', () => {
  const ts = [task(1, { state: 'done' }), task(2, { blocked_by: [1] })];
  const g = taskGraph(ts, { me: 'a' });
  assert.deepEqual(g.nodes.map((n) => n.id), [2]);
  assert.equal(g.edges.length, 0);
  assert.equal(at(g, 2).reason, null);
  assert.equal(taskGraph(ts, { me: 'a', closed: true }).nodes.length, 2);
});

test('an open request to the founder reads "waiting on you"', () => {
  const t = task(3, { requests: [{ id: 9, from: 'a', to: 'human', state: 'landed', at: '2026-01-01T00:00:00Z' }] });
  assert.equal(waitReason(t, 'human', new Set([3])), 'waiting on you');
  assert.equal(waitReason(task(3), 'human', new Set([3])), null);
});

test('mine marks the person looking', () => {
  const g = taskGraph([task(1), task(2, { to: 'b' }), task(3, { blocked_by: [1, 2] })], { me: 'a' });
  assert.deepEqual(g.nodes.map((n) => n.mine), [true, false, true]);
  assert.ok(g.width > 0 && g.height > 0);
});
