import { test } from 'node:test';
import assert from 'node:assert/strict';
import { INVALID, agentTime, bandOf, duration, minutesOf, parseCap, parseMinutes } from '../src/index.ts';

test('the bars change colour at 60 and 85 percent, not before', () => {
  assert.deepEqual([59, 60, 84, 85].map(bandOf), ['ok', 'warn', 'warn', 'bad']);
});

test('time in words', () => {
  assert.deepEqual([0, 50, 90, 2700, 11520].map(duration), ['0 m', '50 s', '1 m', '45 m', '3 h 12 m']);
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
