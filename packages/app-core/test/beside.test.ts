import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dropAgent } from '#src/index.ts';

test('the right half takes a new agent as the second one, and replaces a second one already there', () => {
  assert.deepEqual(dropAgent({ agent: 'a', beside: null }, 'right', 'b'), { agent: 'a', beside: 'b' });
  assert.deepEqual(dropAgent({ agent: 'a', beside: 'b' }, 'right', 'c'), { agent: 'a', beside: 'c' });
});

test('the left half replaces the first agent and keeps the second', () => {
  assert.deepEqual(dropAgent({ agent: 'a', beside: null }, 'left', 'b'), { agent: 'b', beside: null });
  assert.deepEqual(dropAgent({ agent: 'a', beside: 'b' }, 'left', 'c'), { agent: 'c', beside: 'b' });
});

test('dropping the agent the other half shows swaps the halves', () => {
  assert.deepEqual(dropAgent({ agent: 'a', beside: 'b' }, 'left', 'b'), { agent: 'b', beside: 'a' });
  assert.deepEqual(dropAgent({ agent: 'a', beside: 'b' }, 'right', 'a'), { agent: 'b', beside: 'a' });
});

test('dropping an agent on the half that already shows it changes nothing, and one agent cannot sit beside itself', () => {
  assert.equal(dropAgent({ agent: 'a', beside: 'b' }, 'left', 'a'), null);
  assert.equal(dropAgent({ agent: 'a', beside: 'b' }, 'right', 'b'), null);
  assert.equal(dropAgent({ agent: 'a', beside: null }, 'right', 'a'), null);
});
