import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ago, clockTime, dateTime, shortSpan, span } from '#src/index.ts';

test('a span uses its two biggest units, and says so under half a minute', () => {
  assert.deepEqual([20_000, 45 * 60_000, 130 * 60_000, 3 * 86_400_000 + 4 * 3_600_000].map(span), ['under a minute', '45 m', '2 h 10 m', '3 d 4 h']);
  assert.equal(span(3 * 3_600_000), '3 h');
});

test('a short span is one unit, and an age is a short span', () => {
  assert.deepEqual([0, 45, 720, 7200, 90_000, -5].map(shortSpan), ['0s', '45s', '12m', '2h', '1d', '0s']);
  const now = Date.parse('2026-10-15T12:00:00Z');
  assert.equal(ago('2026-10-15T11:55:00Z', now), '5m');
  assert.equal(ago(null, now), '');
});

test('a time that is not a date is empty, not "Invalid Date"', () => {
  assert.equal(clockTime('nope'), '');
  assert.equal(dateTime('nope'), '');
  assert.match(clockTime('2026-10-15T21:42:00Z'), /\d/);
  assert.match(dateTime('2026-10-15T21:42:00Z'), /Oct|10/);
});
