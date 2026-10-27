import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { TimeSegment } from '@handline/client';
import { timeView } from '#src/index.ts';

const seg = (
  kind: TimeSegment['kind'],
  from: string,
  to: string,
  text = '',
  on: TimeSegment['on'] = kind === 'waiting' ? 'founder' : null,
): TimeSegment => ({
  kind,
  on,
  text,
  task: null,
  from,
  to,
  seconds: (Date.parse(to) - Date.parse(from)) / 1000,
});

const segments = [
  seg('working', '2026-10-20T11:00:00Z', '2026-10-20T12:00:00Z'),
  seg('queued', '2026-10-20T10:00:00Z', '2026-10-20T11:00:00Z', 'queued'),
  seg('waiting', '2026-10-20T12:00:00Z', '2026-10-20T12:30:00Z', 'needs you'),
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
    { label: 'working', word: 'Working', seconds: 3 * 3600 },
    { label: 'founder', word: 'Needs you', seconds: 1800 },
    { label: 'queued', word: 'Waiting to start', seconds: 3600 },
  ]);
});
test('a waiting stretch is worded by what it waits on, one total per kind of wait', () => {
  const at = (n: number) => `2026-10-20T${String(10 + n).padStart(2, '0')}:00:00Z`;
  const waits = (['founder', 'task', 'agent', 'account', null] as const).map((on, i) => seg('waiting', at(i), at(i + 1), '', on));
  assert.deepEqual(
    timeView(waits).totals.map((t) => [t.label, t.word]),
    [
      ['founder', 'Needs you'],
      ['task', 'Blocked'],
      ['agent', 'Waiting on an agent'],
      ['account', 'At a usage limit'],
      ['waiting', 'Waiting'],
    ],
  );
  assert.deepEqual(
    timeView(waits).rows.map((r) => r.text),
    ['needs you', 'blocked', 'waiting on an agent', 'at a usage limit', 'waiting'],
  );
});
test('a wait says its own word; only a wait on a task or an agent keeps the daemon line that names it', () => {
  const at = (n: number) => `2026-10-20T${String(10 + n).padStart(2, '0')}:00:00Z`;
  const rows = timeView([
    seg('waiting', at(0), at(1), 'needs you', 'founder'),
    seg('waiting', at(1), at(2), 'blocked by T7', 'task'),
    seg('waiting', at(2), at(3), 'waiting on handline.a', 'agent'),
    seg('waiting', at(3), at(4), 'wall', 'account'),
  ]).rows;
  assert.deepEqual(
    rows.map((r) => r.text),
    ['needs you', 'blocked by T7', 'waiting on handline.a', 'at a usage limit'],
  );
});
test('words a stretch with no reason by its kind, and an empty task has nothing', () => {
  assert.equal(timeView(segments).rows[1]!.text, 'working');
  assert.deepEqual(timeView([]), { rows: [], totals: [], seconds: 0 });
});
