import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mentionAt, mentionNames } from '#src/index.ts';

test('a mention is an @word at the end, at the start or after a space', () => {
  assert.deepEqual(mentionAt('hi @pc'), { query: 'pc', length: 3 });
  assert.deepEqual(mentionAt('@'), { query: '', length: 1 });
  assert.deepEqual(mentionAt('@handline.pc-tasks'), { query: 'handline.pc-tasks', length: 18 });
  assert.equal(mentionAt('mail me@pc'), null);
  assert.equal(mentionAt('@pc and'), null);
});

test('names near the task come first, then prefixes of the last word, then the rest', () => {
  const names = ['handline.pc', 'handline.mobile', 'handline.pc.auditor', 'other.pcb', 'zeta'];
  assert.deepEqual(mentionNames('pc', names, ['handline.pc.auditor']), ['handline.pc.auditor', 'handline.pc', 'other.pcb']);
  assert.deepEqual(mentionNames('', names, ['zeta'], 2), ['zeta', 'handline.mobile']);
  assert.deepEqual(mentionNames('nope', names), []);
});
