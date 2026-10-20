import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { HistoryRow } from '@handline/client';
import { dayLabel, firstLine, historyCounts, historyView, memoAction } from '#src/index.ts';

const row = (at: string, by: string, field: HistoryRow['field'], was: string, now: string, memo?: number): HistoryRow => ({
  at,
  by,
  field,
  was,
  now,
  ...(memo ? { memo } : {}),
});
const rows = [
  row('2026-10-17T11:43:00', 'founder', 'priority', 'P2', 'P1'),
  row('2026-10-17T11:33:00', 'founder', 'assignee', 'a', 'b'),
  row('2026-10-17T11:33:00', 'founder', 'title', 'x', 'y'),
  row('2026-10-17T11:30:00', 'founder', 'memo', 'Skip', '', 2),
  row('2026-10-17T11:21:00', 'handline.pc', 'memo', 'old', 'new', 1),
  row('2026-10-17T11:06:00', 'handline.pc', 'memo', '', 'first', 1),
  row('2026-10-17T11:28:00', 'handline.auditor', 'state', 'doing', 'done'),
  row('2026-10-16T10:41:00', 'founder', 'state', '', 'queued'),
];

test('the chips count their rows', () => {
  assert.deepEqual(historyCounts(rows), { all: 8, fields: 3, state: 2, memo: 3 });
});

test('under All a day’s memo changes fold to one item where the newest sits, the fields of one edit sit under one time', () => {
  const days = historyView(rows, 'all');
  assert.equal(days.length, 2);
  const kinds = days[0]!.items.map((i) => i.kind);
  assert.deepEqual(kinds, ['edit', 'edit', 'memos', 'edit']);
  const edit = days[0]!.items[1]!;
  assert.equal(edit.kind === 'edit' && edit.rows.length, 2);
  const fold = days[0]!.items[2]!;
  assert.equal(fold.kind === 'memos' && fold.rows.length, 3);
});

test('under Memo every memo change is its own item, oldest first when asked', () => {
  const days = historyView(rows, 'memo');
  assert.deepEqual(
    days[0]!.items.map((i) => (i.kind === 'edit' ? i.rows[0]!.memo : 0)),
    [2, 1, 1],
  );
  const old = historyView(rows, 'memo', false);
  assert.equal(old[0]!.items[0]!.kind === 'edit' && old[0]!.items[0]!.rows[0]!.now, 'first');
});

test('a memo row says what was done, and a long text shows its first line', () => {
  assert.deepEqual(rows.filter((r) => r.field === 'memo').map(memoAction), ['deleted', 'edited', 'added']);
  assert.deepEqual(firstLine('one\ntwo'), { head: 'one', more: true });
  assert.deepEqual(firstLine('short'), { head: 'short', more: false });
  assert.equal(firstLine('x'.repeat(100)).more, true);
});

test('days are called Today, Yesterday, then by date', () => {
  const now = Date.parse('2026-10-17T12:00:00');
  assert.deepEqual(
    ['2026-10-17', '2026-10-16'].map((d) => dayLabel(d, now)),
    ['Today', 'Yesterday'],
  );
  assert.match(dayLabel('2026-10-01', now), /Oct/);
});
