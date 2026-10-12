import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Proposal } from '@handline/client';
import { decideMany, raised, settled } from '../src/proposals.ts';

const p = (id: number, over: object = {}): Proposal =>
  ({ id, title: `settings a${id}`, subject: `s${id}`, state: 'open', ...over }) as Proposal;

test('decideMany decides each in order for its subject and sends the note only on reject', async () => {
  const seen: object[] = [];
  const rows = await decideMany([p(1), p(2)], 'reject', 'no', async (a) => void seen.push(a), String);
  assert.deepEqual(seen, [
    { proposal: 1, decision: 'reject', note: 'no', subject: 's1' },
    { proposal: 2, decision: 'reject', note: 'no', subject: 's2' },
  ]);
  assert.ok(rows.every((r) => r.ok));
  const a: object[] = [];
  await decideMany([p(1)], 'approve', '', async (x) => void a.push(x), String);
  assert.deepEqual(a, [{ proposal: 1, decision: 'approve', subject: 's1' }]);
});

test('decideMany records a refusal with its words and goes on with the rest', async () => {
  const rows = await decideMany(
    [p(1), p(2), p(3)],
    'approve',
    '',
    async (a) => {
      if (a.proposal === 2) throw new Error('settled');
    },
    (e) => (e as Error).message,
  );
  assert.deepEqual(
    rows.map((r) => r.ok),
    [true, false, true],
  );
  assert.equal(rows[1]!.error, 'settled');
});

test('decideMany with nothing chosen sends nothing', async () => {
  const none = await decideMany([], 'approve', '', async () => assert.fail('called'), String);
  assert.deepEqual(none, []);
});

test('settled turns an accepted approve whose apply failed into a failed row with the error', () => {
  const sent = [
    { id: 1, title: 'a', ok: true },
    { id: 3, title: 'c', ok: true },
  ];
  const rows = settled(sent, [p(1, { state: 'approved' }), p(3, { state: 'apply_failed', error: 'could not write the file' })]);
  assert.deepEqual(
    rows.map((r) => [r.ok, r.error]),
    [
      [true, undefined],
      [false, 'could not write the file'],
    ],
  );
  assert.equal(settled([{ id: 4, title: 'd', ok: true }], [p(4, { state: 'apply_failed' })])[0]!.error, 'the change was not applied');
});

test('raised keeps open proposals only, newest first, and counts those that did not fit', () => {
  const at = (n: number) => `2026-01-0${n}T00:00:00Z`;
  const l = [
    p(1, { created: at(1) }),
    p(2, { created: at(2) }),
    p(3, { created: at(3) }),
    p(4, { created: at(4), state: 'apply_failed' }),
    p(5, { created: at(5), state: 'approved' }),
  ];
  const r = raised(l, 2);
  assert.deepEqual(
    r.shown.map((x) => x.id),
    [3, 2],
  );
  assert.equal(r.more, 1);
});
