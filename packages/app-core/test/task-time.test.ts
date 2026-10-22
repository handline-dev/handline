import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { TimeSegment } from '@handline/client';
import { timeView } from '#src/index.ts';

const seg = (kind: TimeSegment['kind'], from: string, to: string, text = ''): TimeSegment => ({
  kind,
  on: kind === 'waiting' ? 'founder' : null,
  text,
  task: null,
  from,
  to,
  seconds: (Date.parse(to) - Date.parse(from)) / 1000,
});

const segments = [
  seg('working', '2026-10-20T11:00:00Z', '2026-10-20T12:00:00Z'),
  seg('queued', '2026-10-20T10:00:00Z', '2026-10-20T11:00:00Z', 'queued'),
  seg('waiting', '2026-10-20T12:00:00Z', '2026-10-20T12:30:00Z', 'waiting on you'),
  seg('working', '2026-10-20T12:30:00Z', '2026-10-20T14:30:00Z'),
];
test('orders the segments by time and adds them up with their shares', () => {
  const v = timeView(segments);
  assert.deepEqual(
    v.rows.map((r) => r.kind),
    ['queued', 'working', 'waiting', 'working'],
  );
  assert.equal(v.seconds, 4.5 * 3600);
  assert.ok(Math.abs(v.rows.reduce((n, r) => n + r.share, 0) - 1) < 1e-9);
});
test('totals the kinds the task spent time in, working first', () => {
  assert.deepEqual(timeView(segments).totals, [
    { kind: 'working', word: 'Working', seconds: 3 * 3600 },
    { kind: 'waiting', word: 'Waiting', seconds: 1800 },
    { kind: 'queued', word: 'Up next', seconds: 3600 },
  ]);
});
test('words a stretch with no reason by its kind, and an empty task has nothing', () => {
  assert.equal(timeView(segments).rows[1]!.text, 'working');
  assert.deepEqual(timeView([]), { rows: [], totals: [], seconds: 0 });
});
