import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  INVALID,
  agentTime,
  bandOf,
  minutesOf,
  parseCap,
  parseMinutes,
  parseLimit,
  limitFields,
  parseRange,
  paceFields,
} from '#src/index.ts';

test('the bars change colour at 60 and 85 percent, not before', () => {
  assert.deepEqual([59, 60, 84, 85].map(bandOf), ['ok', 'warn', 'warn', 'bad']);
});

test('agent time is longest first and folds the idle ones', () => {
  assert.deepEqual(agentTime({ a: 10, b: 0, c: 99, d: 0 }), {
    rows: [
      { agent: 'c', seconds: 99 },
      { agent: 'a', seconds: 10 },
    ],
    idle: 2,
  });
});

test('the cap field clears when empty and refuses anything but a whole number from 1', () => {
  assert.equal(parseCap(''), null);
  assert.equal(parseCap(' 6 '), 6);
  for (const bad of ['0', '-1', '2.5', 'four', '1e3']) assert.equal(parseCap(bad), INVALID, bad);
});

test('idle close converts minutes to seconds and back; 0 is never; junk is refused', () => {
  assert.equal(parseMinutes('30'), 1800);
  assert.equal(parseMinutes('0'), 0);
  assert.equal(parseMinutes(''), null);
  assert.equal(parseMinutes('1.5'), INVALID);
  assert.equal(minutesOf(1800), '30');
  assert.equal(minutesOf(undefined), '');
});

test('a machine limit takes a whole number, off as 0, empty as the default; a daemon without the keys has no rows', () => {
  assert.deepEqual(['0', 'off', ' OFF ', '150', ''].map(parseLimit), [0, 0, 0, 150, null]);
  assert.equal(parseLimit('1.5'), INVALID);
  assert.equal(parseLimit('lots'), INVALID);
  assert.deepEqual(limitFields({}), []);
  const [agents, load] = limitFields({ host_active_cap: 0, load_per_core_pct: 150 });
  assert.deepEqual([agents!.current, load!.current, load!.change(200)], ['off', '150', { load_per_core_pct: 200 }]);
});

test('a pace number is a whole number inside its range, empty clears it, anything else is not sent', () => {
  const target = parseRange(50, 100);
  assert.deepEqual(['50', '100', ' 75 ', ''].map(target), [50, 100, 75, null]);
  assert.deepEqual(['49', '101', '7.5', 'x', '-60'].map(target), Array(5).fill(INVALID));
  assert.equal(parseRange(0, 50)('0'), 0);
});

test('the pace rows are the target and the burst, and a daemon without the keys has none', () => {
  assert.deepEqual(paceFields({}), []);
  const rows = paceFields({ pacing: true, pace_target_pct: 98, pace_burst_pct: 10 });
  assert.deepEqual(
    rows.map((r) => [r.label, r.current]),
    [
      ['Pace target', '98'],
      ['Pace burst', '10'],
    ],
  );
  assert.deepEqual(rows[1]!.change(20), { pace_burst_pct: 20 });
});
