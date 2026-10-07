import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FIELDS, cronWords, mayChange, problemWith } from '../src/index.ts';

const field = (key: string) => FIELDS.find((f) => f.key === key)!;

test('the founder changes any field; anyone else only the keys an agent may change on itself', () => {
  assert.equal(mayChange('repo', 'founder'), true);
  assert.equal(mayChange('repo', 'member'), false);
  assert.equal(mayChange('sandbox', 'agent'), false);
  for (const k of ['model', 'effort', 'context_k', 'handoff_note'] as const) assert.equal(mayChange(k, 'agent'), true);
});
test('a number field takes whole numbers only, and says what is allowed', () => {
  assert.equal(problemWith(field('context_k'), '300'), null);
  assert.match(problemWith(field('context_k'), 'abc')!, /whole number/);
  assert.match(problemWith(field('context_k'), '0')!, /at least 1/);
  assert.equal(problemWith(field('idle_close'), '0'), null);
  assert.match(problemWith(field('idle_close'), '1.5')!, /whole minutes/);
  assert.match(problemWith(field('model'), '  ')!, /model name/);
});
test('cron lines in words for the common shapes, null for the rest', () => {
  assert.equal(cronWords('0 9 * * 1-5'), 'every weekday 09:00');
  assert.equal(cronWords('30 7 * * *'), 'every day 07:30');
  assert.equal(cronWords('*/5 * * * *'), null);
});
