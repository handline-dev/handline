import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Headroom } from '@handline/client';
import { headroomStamp, headroomSummary, nearestPoint } from '#src/index.ts';

const h = (over: Partial<Headroom> = {}): Headroom => ({
  now: '2026-10-07T12:00:00Z',
  step_s: 900,
  combined: {
    past: [
      { at: '2026-10-07T11:45:00Z', left_pct: 61.4 },
      { at: '2026-10-07T12:00:00Z', left_pct: null },
    ],
    future: [],
  },
  accounts: [],
  runs_out_at: null,
  next_reset: null,
  ...over,
});

test('the summary reads the last point with data, and says when nothing runs out or resets', () => {
  assert.equal(headroomSummary(h()), "61% left · won't run out in this span · no reset ahead");
});

test('the summary names the next reset and how far off it is', () => {
  const s = headroomSummary(h({ next_reset: { at: '2026-10-07T14:10:00Z', account: 'alpha', window: '5h' } }));
  assert.match(s, /next reset in 2 h 10 m \(alpha, 5 h\)$/);
});

test('the nearest point skips the ones with no data', () => {
  const pts = [
    { at: '2026-10-07T12:00:00Z', pct: 10 },
    { at: '2026-10-07T12:15:00Z', pct: null },
    { at: '2026-10-07T12:30:00Z', pct: 30 },
  ];
  const t = Date.parse('2026-10-07T12:14:00Z');
  assert.equal(nearestPoint(pts, t, (p) => p.pct !== null)?.pct, 10);
  assert.equal(nearestPoint(pts, t)?.pct, null);
  assert.equal(nearestPoint([], t), null);
});

test('the balloon text says no data for a gap', () => {
  assert.match(headroomStamp('2026-10-07T12:00:00Z', null), /no data$/);
  assert.match(headroomStamp('2026-10-07T12:00:00Z', 42.6), /43%$/);
});
