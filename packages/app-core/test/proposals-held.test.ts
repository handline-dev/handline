import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Proposal } from '@handline/client';
import { heldLine, isHeld, isWaiting, ordered, waitingCount } from '#src/proposals.ts';

const p = (id: number, over: object = {}): Proposal =>
  ({ id, title: `settings a${id}`, subject: `s${id}`, state: 'open', created: `2026-10-0${id}T00:00:00Z`, ...over }) as Proposal;

test('only an applying proposal is held, and a held one is not waiting', () => {
  assert.deepEqual(
    ['open', 'apply_failed', 'applying', 'approved', 'rejected', 'withdrawn', 'later'].map((state) => isHeld(p(1, { state }))),
    [false, false, true, false, false, false, false],
  );
  assert.equal(isWaiting(p(1, { state: 'applying' })), false);
});

test('a held proposal is not counted and stays out of the waiting group, which the bulk verbs act on', () => {
  const l = [p(3), p(2, { state: 'applying' }), p(1, { state: 'approved', settled: '2026-10-04T00:00:00Z' })];
  assert.deepEqual(
    ordered(l).waiting.map((x) => x.id),
    [3],
  );
  assert.equal(waitingCount(l), 1);
});

test('an unknown state is neither waiting nor held, as before', () => {
  const l = [p(1, { state: 'later' })];
  assert.equal(waitingCount(l), 0);
  assert.deepEqual(ordered(l), { waiting: [], recent: l });
});

test('heldLine names the agents whose turn it waits for', () => {
  assert.equal(heldLine(p(1, { state: 'applying', waiting_on: ['handline.auditor'] })), 'Held until handline.auditor finishes its turn.');
  assert.equal(heldLine(p(1, { state: 'applying', waiting_on: ['a', 'b'] })), 'Held until a and b finish their turns.');
  assert.equal(heldLine(p(1, { state: 'applying', waiting_on: ['a', 'b', 'c'] })), 'Held until a, b and c finish their turns.');
  assert.equal(heldLine(p(1, { state: 'applying' })), 'Held until a turn ends.');
});

test('heldLine says since when it waits', () => {
  const now = Date.parse('2026-10-26T12:10:00Z');
  const held = p(1, { state: 'applying', waiting_on: ['a'], waiting_since: '2026-10-26T12:00:00Z' });
  assert.equal(heldLine(held, now), "Held until a finishes its turn, since 10m.");
});
