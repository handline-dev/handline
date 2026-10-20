import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dotOf, dotOfStatus, statusLine } from '../src/index.ts';

const st = (state: string, reason: string, text = 'x') => ({ state, reason, text, until: null, since: null, tasks: [] });

test('draws the dot from the state and reason', () => {
  assert.equal(dotOfStatus(st('preparing', 'restart')), 'waking');
  assert.equal(dotOfStatus(st('waiting', 'you')), 'waiting');
  assert.equal(dotOfStatus(st('waiting', 'clock')), 'parked');
  assert.equal(dotOfStatus(st('waiting', 'task')), 'blocked');
  assert.equal(dotOfStatus(st('stopped', 'wall')), 'blocked');
  assert.equal(dotOfStatus(st('off', 'none')), 'none');
  assert.equal(dotOfStatus(st('mystery', 'none')), null);
});

test('an agent view status decides its dot; an unknown state falls back to the old words', () => {
  const a = { state: 'idle', has_dialog: false, block: null, handoff: null };
  assert.equal(dotOf({ ...a, status: st('running', 'turn') }), 'running');
  assert.equal(dotOf({ ...a, status: st('mystery', 'none') }), 'idle');
  assert.equal(dotOf(a), 'idle');
});

test('shows the text as it is', () => {
  assert.equal(statusLine(st('waiting', 'task', 'Waiting on T5028')), 'Waiting on T5028');
  assert.equal(statusLine(undefined), '');
});
