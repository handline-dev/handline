import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Task } from '@handline/client';
import { taskAfter } from '../src/tasks.ts';

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
