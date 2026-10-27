import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Account } from '@handline/client';
import { alertText, forecastWords, runsOutSoon, weekForecast } from '#src/index.ts';

const NOW = Date.parse('2026-10-27T00:00:00Z');
const H = 3_600_000;
const at = (h: number) => new Date(NOW + h * H).toISOString();
const acct = (used: number, resetsInH = 48) => ({ weekly: { used_pct: used, resets_at: at(resetsInH) } }) as Account;
const line = (...pts: [number, number | null][]) => ({ future: pts.map(([h, left_pct]) => ({ at: at(h), left_pct })) });

test('steady use that lasts: left is the average and the week lasts to the reset', () => {
  const f = weekForecast([acct(30), acct(46)], line([12, 50], [48, 20]), NOW);
  assert.equal(f.leftPct, 62);
  assert.equal(f.kind, 'lasts');
  assert.equal(forecastWords(f, String), 'lasts until the week resets');
  assert.equal(runsOutSoon(f, 24), false);
});

test('rising use reaches nothing before the reset: it runs out, with how long before the reset', () => {
  const f = weekForecast([acct(80)], line([6, 10], [10, 0], [48, 0]), NOW);
  assert.equal(f.kind, 'runs_out');
  assert.equal(f.runsOutInMs, 10 * H);
  assert.equal(
    forecastWords(f, () => 'Thu 05:00'),
    'runs out Thu 05:00, 1 d 14 h before it resets',
  );
  assert.equal(runsOutSoon(f, 24), true);
  assert.equal(runsOutSoon(f, 0), false); // off
  assert.equal(runsOutSoon(f, 6), false);
  assert.match(alertText(f), /runs out in about 10 h \(before it resets in 2 d\)/);
});

test('a flat line is a week that lasts; no data or no accounts is unknown', () => {
  assert.equal(weekForecast([acct(10)], line([24, 90], [48, 90]), NOW).kind, 'lasts');
  assert.equal(weekForecast([acct(10)], line([24, null]), NOW).kind, 'unknown');
  assert.equal(weekForecast([acct(10)], undefined, NOW).kind, 'unknown');
  const none = weekForecast([], line([1, 5]), NOW);
  assert.deepEqual([none.leftPct, none.kind], [null, 'unknown']);
});
