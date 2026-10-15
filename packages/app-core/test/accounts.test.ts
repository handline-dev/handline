import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Account } from '@handline/client';
import { FIVE_HOURS_MS, gaugeOf, headroom, outUntil, stateOf, toneOf } from '../src/index.ts';

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

test('headroom: amber from 90% or a stop before the reset, red from 95% or under 15 minutes, no stop time when none is given', () => {
  const now = Date.parse('2026-10-15T00:00:00Z');
  const w = (used_pct: number, hours = 3) => ({ used_pct, resets_at: new Date(now + hours * 3_600_000).toISOString(), source: 'measured' });
  assert.deepEqual(headroom(w(40), null, now), { tone: 'ok', stopAt: null });
  assert.equal(headroom(w(89), null, now).tone, 'ok');
  assert.equal(headroom(w(90), null, now).tone, 'warn');
  assert.equal(headroom(w(95), null, now).tone, 'bad');
  assert.equal(headroom(w(40), 2 * 3600, now).tone, 'warn'); // stops in 2 h, before the reset in 3 h
  assert.deepEqual(headroom(w(40), 4 * 3600, now), { tone: 'ok', stopAt: null }); // the window resets first: no stop line
  assert.equal(headroom(w(40), 3 * 3600, now).stopAt, null); // at the reset
  assert.equal(headroom(w(40), 14 * 60, now).tone, 'bad');
  assert.equal(headroom(w(40), 15 * 60, now).tone, 'warn');
  assert.equal(headroom(w(40), 2 * 3600, now).stopAt, now + 2 * 3600_000);
  assert.equal(headroom(null, null, now).tone, 'ok');
});
