import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Task } from '@handline/client';
import { agentTasks, dueOf, taskAfter } from '../src/tasks.ts';

const task = (state: string): Task => ({
  id: 7,
  from: 'founder',
  to: 'a',
  title: 't',
  state,
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
});

test('done, queue and draft move a task only along transitions the daemon is known to make', () => {
  assert.equal(taskAfter('done', task('doing'), 'now')?.state, 'done');
  assert.equal(taskAfter('done', task('doing'), 'now')?.done, 'now');
  assert.equal(taskAfter('queue', task('draft'), 'now')?.state, 'queued');
  assert.equal(taskAfter('draft', task('queued'), 'now')?.state, 'draft');
  assert.equal(taskAfter('done', task('discarded'), 'now'), undefined);
  assert.equal(taskAfter('queue', task('doing'), 'now'), undefined);
});

test('an edit changes only the fields it names', () => {
  const after = taskAfter('edit', task('queued'), 'now', { title: 'new' })!;
  assert.equal(after.title, 'new');
  assert.equal(after.to, 'a');
  assert.equal(taskAfter('edit', task('queued'), 'now', { owner: 'b' })!.to, 'b');
});

test('a verb whose result depends on the daemon changes nothing', () => {
  for (const verb of ['reopen', 'release', 'withdraw', 'cancel'] as const) assert.equal(taskAfter(verb, task('doing'), 'now'), undefined);
});

const row = (id: number, over: Partial<Task> = {}): Task =>
  ({ id, from: 'boss', to: 'me', title: `t${id}`, state: 'queued', time: null, ...over }) as Task;

test('agentTasks lists what the agent holds and what it opened, once each, with which it is', () => {
  const NOW = Date.parse('2026-10-12T12:00:00Z');
  const items = [
    row(1),
    row(2, { from: 'me', to: 'other' }),
    row(3, { from: 'me', to: 'me' }),
    row(4, { from: 'x', to: 'y' }),
    row(5, { state: 'done' }),
    row(6, { time: { length_s: 60, default: false, start: null, end: '2026-10-12T11:00:00Z', overdue_since: null, told: 0 } }),
  ];
  const got = agentTasks([...items, items[0]!], 'me', NOW);
  assert.deepEqual(
    got.map((r) => [r.task.id, r.role]),
    [
      [6, 'owner'],
      [3, 'owner'],
      [2, 'assigned'],
      [1, 'owner'],
      [5, 'owner'],
    ],
  );
});

test('dueOf says the time left, then how long overdue, and nothing for a task with no clock or an ended one', () => {
  const NOW = Date.parse('2026-10-12T12:00:00Z');
  const clock = (end: string) => ({ length_s: 1, default: false, start: null, end, overdue_since: null, told: 0 });
  assert.deepEqual(dueOf(row(1, { time: clock('2026-10-12T14:30:00Z') }), NOW), { text: '2h left', over: false });
  assert.deepEqual(dueOf(row(1, { time: clock('2026-10-12T12:00:30Z') }), NOW), { text: '<1m left', over: false });
  assert.deepEqual(dueOf(row(1, { time: clock('2026-10-09T12:00:00Z') }), NOW), { text: '3d overdue', over: true });
  assert.deepEqual(dueOf(row(1, { time: { ...clock('2026-10-12T14:00:00Z'), overdue_since: '2026-10-12T11:00:00Z' } }), NOW), {
    text: '1h overdue',
    over: true,
  }); // the daemon's word wins
  assert.equal(dueOf(row(1), NOW), null);
  assert.equal(dueOf(row(1, { state: 'done', time: clock('2026-10-09T12:00:00Z') }), NOW), null);
});
