import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Proposal } from '@handline/client';
import { changesToggle, isBundle, plainText } from '#src/proposals.ts';

const p = (over: object = {}): Proposal => ({ id: 1, title: 'settings a', diff: '- x\n+ y', state: 'open', ...over }) as Proposal;

test('message numbers are not shown, the words around them stay tidy', () => {
  assert.equal(plainText('tidy the team (msg_8aebff7), as asked in msg_1a2b3c4 .'), 'tidy the team, as asked in.');
  assert.equal(plainText('see ocp-msg 8aebff7a12 now'), 'see now');
  assert.equal(plainText('no number here'), 'no number here');
});

test('a bundle counts its changes in the toggle, any other proposal says diff', () => {
  const b = p({ kind: 'bundle', diff: '- a\n- b\n+ c' });
  assert.equal(isBundle(b), true);
  assert.deepEqual([changesToggle(b, false), changesToggle(b, true)], ['Show 3 changes', 'Hide changes']);
  assert.deepEqual([changesToggle(p(), false), changesToggle(p(), true)], ['Show diff (2 changes)', 'Hide diff']);
});
