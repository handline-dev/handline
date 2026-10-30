import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Resources } from '@handline/client';
import { bytesText, limitSentence, loadFull, memoryLow, resourceCells, useRows } from '#src/index.ts';

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
    heavy_running: 0,
    ...m,
  },
});

test('bytes read as the shortest figure', () => {
  assert.deepEqual([3.6 * GB, 120 * GB, 420 * 2 ** 20].map(bytesText), ['3.6 GB', '120 GB', '420 MB']);
});

test('the bar cells say the CPU, the memory free and the disk left, and a cell over its limit says so in words', () => {
  const r = res({ ram_free_bytes: 9.8 * GB, load_per_core: 2.8, ram_free_pct: 30, disk_free_bytes: 50 * GB });
  r.agents_total = { cpu_pct: 240, rss_bytes: 5.6 * GB };
  const cells = resourceCells(r)!;
  assert.deepEqual(
    cells.map((c) => c.text),
    ['CPU 24% · Load full', 'Memory 9.8 GB free', 'Disk 50 GB left'],
  );
  assert.deepEqual(
    cells.map((c) => c.warn),
    [true, false, false],
  );
  const calm = resourceCells(res({ load_per_core: 0.4, ram_free_pct: 40, disk_free_bytes: 100 * GB }))!;
  assert.equal(
    calm.some((c) => c.warn),
    false,
  );
  const tight = resourceCells(res({ ram_free_bytes: 1.1 * GB, ram_free_pct: 3, disk_free_bytes: 4 * GB }))!;
  assert.equal(tight[1]!.text, 'Memory low 1.1 GB free');
  assert.equal(tight[2]!.text, 'Disk 4.0 GB left · Low');
});

test('a limit of never holds nothing, and the memory floor holds under it', () => {
  const r = res({ load_per_core: 5, ram_free_pct: 8 });
  assert.equal(loadFull(r, 0), false);
  assert.equal(loadFull(r, 150), true);
  assert.equal(memoryLow(r, 10), true);
  assert.equal(memoryLow(r, 0), false);
  assert.equal(limitSentence('2.8', true, false), 'Now 2.8 → new starts wait.');
  assert.equal(limitSentence('1.2', false, false), 'Now 1.2 → starts go.');
});

test('nothing is shown before the daemon has measured', () => {
  assert.equal(resourceCells(null), null);
  assert.equal(resourceCells(undefined), null);
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
  assert.equal(total, '3 agents · CPU 97% · memory 800 MB');
});
