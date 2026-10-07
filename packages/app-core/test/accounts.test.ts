import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Account } from '@handline/client';
import { FIVE_HOURS_MS, gaugeOf, outUntil, span, stateOf, toneOf } from '../src/index.ts';

const NOW = Date.parse('2026-10-07T12:00:00Z');
const at = (ms: number) => new Date(NOW + ms).toISOString();
const acct = (over: Partial<Account> = {}): Account =>
  ({ name: 'a', login_state: 'ok', five_hour: null, weekly: null, agents: [], ...over }) as Account;

test('the bar changes colour at 60 and at 85 percent, not before', () => {
  assert.deepEqual([59, 60, 84, 85].map(toneOf), ['ok', 'warn', 'warn', 'bad']);
});

test('a gauge says how far through the window we are, the reset, and the runway at the pace so far', () => {
  // 2 h left of 5 h: 3 h in, 30% used, so 7 h of use would fill it: the reset (2 h) comes first.
  const g = gaugeOf({ used_pct: 30, resets_at: at(2 * 3_600_000), source: 'measured' }, FIVE_HOURS_MS, NOW);
  assert.equal(g.through, 60);
  assert.equal(g.resetIn, 2 * 3_600_000);
  assert.equal(g.runway, null);
  // 80% after 3 h: 45 more minutes at that pace, before the reset.
  const hot = gaugeOf({ used_pct: 80, resets_at: at(2 * 3_600_000), source: 'estimated' }, FIVE_HOURS_MS, NOW);
  assert.equal(hot.runway, 45 * 60_000);
  assert.equal(hot.source, 'estimated');
});

test('a window with no reset or no use has no runway, and a percentage is held to 0..100', () => {
  assert.equal(gaugeOf({ used_pct: 50, resets_at: null, source: 'x' }, FIVE_HOURS_MS, NOW).runway, null);
  assert.equal(gaugeOf({ used_pct: 0, resets_at: at(1000), source: 'x' }, FIVE_HOURS_MS, NOW).runway, null);
  assert.equal(gaugeOf({ used_pct: 140, resets_at: null, source: 'x' }, FIVE_HOURS_MS, NOW).pct, 100);
});

test('time in words uses its two biggest units', () => {
  assert.deepEqual([20_000, 45 * 60_000, 130 * 60_000, 3 * 86_400_000 + 4 * 3_600_000].map(span), [
    'under a minute',
    '45 m',
    '2 h 10 m',
    '3 d 4 h',
  ]);
});

test('the state chip: login first, then out, then in use or idle', () => {
  assert.equal(stateOf(acct({ login_state: 'needs_login' })), 'needs_login');
  assert.equal(stateOf(acct({ five_hour: { used_pct: 100, resets_at: at(1), source: 'x' } })), 'out');
  assert.equal(stateOf(acct({ agents: ['a.b'] })), 'in_use');
  assert.equal(stateOf(acct()), 'idle');
  assert.equal(stateOf(acct({ login_state: 'unknown' })), 'unknown');
});

test('an account that is out comes back at the latest reset of its full windows', () => {
  const a = acct({ five_hour: { used_pct: 100, resets_at: at(5), source: 'x' }, weekly: { used_pct: 100, resets_at: at(9), source: 'x' } });
  assert.equal(outUntil(a), at(9));
  assert.equal(outUntil(acct()), null);
});
