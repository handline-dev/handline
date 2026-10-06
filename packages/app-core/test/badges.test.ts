import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentDetail, AgentView } from '@handline/client';
import { badgesOf, needsAttention } from '../src/index.ts';

const view = (over: Partial<AgentDetail> = {}): AgentDetail =>
  ({ name: 'a', state: 'idle', has_dialog: false, block: null, terminal: 'term:a', input_holders: [], ...over }) as AgentDetail;
const kinds = (a: AgentView, me = 'me') => badgesOf(a, me).map((b) => b.kind);

test('badges come from the parsed facts, one row of the design table each', () => {
  assert.deepEqual(kinds(view()), []);
  assert.deepEqual(kinds(view({ has_dialog: true })), ['dialog']);
  assert.deepEqual(kinds(view({ state: 'stalled' })), ['stalled']);
  assert.deepEqual(kinds(view({ block: { kind: 'wall', since: 't' } })), ['stalled']);
  assert.deepEqual(kinds(view({ input_holders: [{ kind: 'person', name: 'me', since: 't' }] })), ['you']);
  assert.deepEqual(kinds(view({ input_holders: [{ kind: 'device', name: 'phone', since: 't' }] })), ['held']);
  assert.deepEqual(kinds(view({ input_holders: [{ kind: 'daemon', name: 'daemon', since: null }] })), []);
  assert.deepEqual(kinds(view({ terminal: null, state: 'no_terminal' })), ['no_terminal']);
});

test('a stall says why and since when; a held badge names the holder', () => {
  const b = badgesOf(view({ block: { kind: 'wall', since: '2026-10-07T01:00:00Z' } }), 'me');
  assert.equal(b[0]!.tip, 'wall since 2026-10-07T01:00:00Z');
  assert.equal(badgesOf(view({ input_holders: [{ kind: 'device', name: 'phone', since: null }] }), 'me')[0]!.label, 'held: phone');
});

test('a state word this client does not know is still said, and dialogs and stalls need attention', () => {
  const b = badgesOf(view({ state: 'brand_new' }), 'me');
  assert.deepEqual(b, [{ kind: 'other', label: 'brand_new', tip: 'state: brand_new' }]);
  assert.equal(needsAttention(b), false);
  assert.equal(needsAttention(badgesOf(view({ has_dialog: true }), 'me')), true);
  assert.equal(needsAttention(badgesOf(view({ state: 'blocked' }), 'me')), true);
});
