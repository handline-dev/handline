import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Task } from '@handline/client';
import { applyEach, plan, rangeBetween, summary, whyNot } from '../src/index.ts';

const task = (id: number, state: string, from = 'founder'): Task => ({ id, state, from, to: 'a', title: `t${id}` }) as Task;

test('a verb applies by the state of the task and who asked', () => {
  assert.equal(whyNot('queue', task(1, 'draft'), 'founder'), null);
  assert.match(whyNot('queue', task(1, 'queued'), 'founder')!, /not a draft/);
  assert.equal(whyNot('draft', task(1, 'queued'), 'founder'), null);
  assert.equal(whyNot('release', task(1, 'doing'), 'founder'), null);
  assert.match(whyNot('release', task(1, 'queued'), 'founder')!, /not running/);
  assert.equal(whyNot('reopen', task(1, 'done'), 'founder'), null);
  assert.match(whyNot('reopen', task(1, 'done', 'x'), 'founder')!, /you asked/);
  assert.match(whyNot('done', task(1, 'discarded'), 'founder')!, /already closed/);
  assert.match(whyNot('done', task(1, 'draft'), 'founder')!, /draft/);
  assert.equal(whyNot('withdraw', task(1, 'queued'), 'founder'), null);
  assert.match(whyNot('withdraw', task(1, 'doing'), 'founder')!, /has started/);
});

test('a plan splits the marked tasks into those it runs on and those it skips with the reason', () => {
  const p = plan('done', [task(1, 'doing'), task(2, 'done'), task(3, 'queued')], 'founder');
  assert.deepEqual(
    p.run.map((t) => t.id),
    [1, 3],
  );
  assert.deepEqual(
    p.skip.map((s) => s.task.id),
    [2],
  );
  assert.match(p.skip[0]!.why, /#2 is already closed/);
});

test('applyEach gives every task its own outcome: one refusal does not stop the rest', async () => {
  const seen: number[] = [];
  const out = await applyEach([1, 2, 3], async (id) => {
    seen.push(id);
    if (id === 2) throw new Error('refused: not yours');
  });
  assert.deepEqual(seen, [1, 2, 3]);
  assert.deepEqual(out, [
    { task: 1, ok: true },
    { task: 2, ok: false, why: 'refused: not yours' },
    { task: 3, ok: true },
  ]);
  assert.equal(summary(out), '2 of 3 changed');
  assert.equal(summary([out[0]!]), 'all 1 changed');
  assert.equal(summary([out[1]!]), 'none of 1 changed');
});

test('shift-click marks the rows between, in either direction, and a lone click marks one', () => {
  const order = [10, 11, 12, 13, 14];
  assert.deepEqual(rangeBetween(order, 11, 13), [11, 12, 13]);
  assert.deepEqual(rangeBetween(order, 13, 11), [11, 12, 13]);
  assert.deepEqual(rangeBetween(order, null, 12), [12]);
  assert.deepEqual(rangeBetween(order, 99, 12), [12]);
  assert.deepEqual(rangeBetween(order, 11, 99), []);
});
