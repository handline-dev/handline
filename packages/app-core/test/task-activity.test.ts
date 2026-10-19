import { test } from 'node:test';
import assert from 'node:assert/strict';
import { activityOf, foldEvents } from '../src/task-activity.ts';

const e = (kind: string, who: string, text: string, at: string, memo: number | null = null) =>
  ({ at, who, kind, text, memo, edited_at: null }) as Parameters<typeof activityOf>[1][number];

test('entries become comments and events in words, a handoff one line with its note kept', () => {
  const a = activityOf(
    [{ id: 7, task: 1, author: 'founder', at: '2026-01-01T00:00:03Z', text: 'Hello' }],
    [
      e('state', 'handline.pc', 'new -> opened', '2026-01-01T00:00:01Z'),
      e('handoff', 'handline.pc → worker2', 'Over to you', '2026-01-01T00:00:02Z'),
      e('memo', 'founder', 'Hello', '2026-01-01T00:00:03Z', 7),
      e('state', 'worker2', 'queued -> doing', '2026-01-01T00:00:04Z'),
      e('reject', 'handline.pc', 'Not yet', '2026-01-01T00:00:05Z'),
    ],
  );
  assert.deepEqual(
    a.map((i) => [i.kind, i.line]),
    [
      ['event', 'handline.pc opened it'],
      ['event', 'handline.pc handed it to worker2'],
      ['comment', ''],
      ['event', 'worker2 started it'],
      ['event', 'handline.pc sent it back'],
    ],
  );
  assert.equal(a[1]!.note, 'Over to you');
  assert.equal(a[2]!.memo, 7);
});

test('only the last three events show until the rest are asked for, comments always', () => {
  const a = activityOf(
    [{ id: 1, task: 1, author: 'y', at: '2026-01-01T00:00:00Z', text: 'hi' }],
    [1, 2, 3, 4, 5].map((n) => e('state', 'x', 'a -> doing', `2026-01-01T00:00:0${n}Z`)),
  );
  const shut = foldEvents(a, false);
  assert.equal(shut.hidden, 2);
  assert.equal(shut.items.length, 4);
  assert.equal(shut.items.filter((i) => i.kind === 'comment').length, 1);
  assert.deepEqual([foldEvents(a, true).hidden, foldEvents(a, true).items.length], [0, 6]);
});
