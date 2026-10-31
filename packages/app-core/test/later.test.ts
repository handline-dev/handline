import assert from 'node:assert/strict';
import { test } from 'node:test';
import { laterChoices, laterHolds, laterKeyOf, laterWords } from '#src/later.ts';

test('a task, a comment ask and the machine can be put off; a prompt and a dialog cannot', () => {
  assert.equal(laterKeyOf({ kind: 'task', task: 42, memo: null }), 'task:42');
  assert.equal(laterKeyOf({ kind: 'decision', task: 42, memo: 7 }), 'comment:7');
  assert.equal(laterKeyOf({ kind: 'machine', task: null, memo: null }), 'machine:disk');
  assert.equal(laterKeyOf({ kind: 'approval', task: null, memo: null }), null);
  assert.equal(laterKeyOf({ kind: 'dialog', task: 3, memo: null }), null);
});

test('a deferral holds until its time passes or its task ends', () => {
  const now = Date.parse('2026-10-30T12:00:00Z');
  assert.equal(
    laterHolds({ until: '2026-10-30T15:00:00Z' }, now, () => false),
    true,
  );
  assert.equal(
    laterHolds({ until: '2026-10-30T11:00:00Z' }, now, () => true),
    false,
  );
  assert.equal(
    laterHolds({ after_task: 9 }, now, (t) => t === 9),
    true,
  );
  assert.equal(
    laterHolds({ after_task: 9 }, now, () => false),
    false,
  );
  assert.equal(
    laterHolds(null, now, () => true),
    false,
  );
});

test('the menu offers the afternoon only before 15:00, then tomorrow and next Monday at 09:00', () => {
  const morning = new Date(2026, 9, 30, 10).getTime(); // a Friday
  const [a, t, m] = laterChoices(morning);
  assert.equal(a!.label, 'This afternoon 15:00');
  assert.equal(new Date(a!.until).getHours(), 15);
  assert.equal(new Date(t!.until).getDate(), 31);
  assert.equal(new Date(m!.until).getDay(), 1);
  assert.equal(new Date(m!.until).getDate(), 2);
  const evening = new Date(2026, 9, 30, 18).getTime();
  assert.deepEqual(
    laterChoices(evening).map((c) => c.label),
    ['Tomorrow 09:00', 'Monday 09:00'],
  );
});

test('a task deferral reads as "when T9 is done"', () => {
  assert.equal(laterWords({ after_task: 9 }), 'when T9 is done');
  assert.match(laterWords({ until: '2026-10-30T15:00:00' }), /\d/);
});
