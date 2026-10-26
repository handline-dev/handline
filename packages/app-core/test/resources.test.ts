import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Resources } from '@handline/client';
import { bytesText, machineCells, useRows } from '#src/index.ts';

const GB = 2 ** 30;
const res = (m: Partial<Resources['machine']> = {}, agents: Resources['agents'] = {}): Resources => ({
  at: '2026-10-19T00:00:00Z',
  agents,
  agents_total: { cpu_pct: 0, rss_bytes: 0 },
  start_hold: null,
  machine: {
    ram_total_bytes: 32 * GB,
    ram_used_bytes: 17 * GB,
    ram_free_bytes: 15 * GB,
    load_1m: 3,
    cores: 10,
    load_per_core: 0.3,
    ram_free_pct: 47,
    disk_free_bytes: 3.6 * GB,
    disk_total_bytes: 500 * GB,
    ...m,
  },
});

test('bytes read as the shortest figure', () => {
  assert.deepEqual([3.6 * GB, 120 * GB, 420 * 2 ** 20].map(bytesText), ['3.6GB', '120GB', '420MB']);
});

test('the bar says memory left, CPU and disk left, and goes red per cell only when over', () => {
  assert.deepEqual(machineCells(res({ disk_free_bytes: 50 * GB })), [
    { text: 'memory 47% left', over: false },
    { text: 'CPU ok', over: false },
    { text: 'disk 50GB left', over: false },
  ]);
  const hot = machineCells(res({ ram_free_bytes: 2 * GB, load_1m: 9.5 }))!;
  assert.deepEqual(
    hot.map((c) => c.over),
    [true, true, true],
  );
  assert.equal(hot[1]!.text, 'CPU high');
});

test('nothing is shown before the daemon has measured', () => {
  assert.equal(machineCells(null), null);
  assert.equal(machineCells(undefined), null);
});

test('agents are heaviest first with a totals line', () => {
  const { rows, total } = useRows(
    res(
      {},
      {
        a: { cpu_pct: 1, rss_bytes: 100 * 2 ** 20 },
        b: { cpu_pct: 95, rss_bytes: 400 * 2 ** 20 },
        c: { cpu_pct: 1, rss_bytes: 300 * 2 ** 20 },
      },
    ),
  );
  assert.deepEqual(
    rows.map((r) => r.agent),
    ['b', 'c', 'a'],
  );
  assert.equal(total, '3 agents · CPU 97% · memory 800MB');
});
