import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Account } from '@handline/client';
import { paceLine } from '#src/index.ts';

const pace = (state: string, back_at: string | null = null) =>
  ({ pace: { state, line_pct: 50, used_pct: 55, rate_pct_h: 0.3, back_at } }) as Pick<Account, 'pace'>;

test('paceLine says what each pace state means for starting agents', () => {
  assert.equal(paceLine(pace('behind')), 'on pace');
  assert.equal(paceLine(pace('ahead')), 'ahead of pace: fewer agents start');
  assert.match(paceLine(pace('over', '2026-10-27T09:00:00Z'))!, /^over pace: no new turns until \S/);
});

test('paceLine is null with pacing off, no pace, or a state it does not know', () => {
  assert.equal(paceLine(pace('off')), null);
  assert.equal(paceLine({ pace: null }), null);
  assert.equal(paceLine({}), null);
  assert.equal(paceLine(pace('later')), null);
});

test('an account over its pace with no back_at still says no new turns', () => {
  assert.equal(paceLine(pace('over')), 'over pace: no new turns');
});

test('the short pace line fits a row: one word, and for over the time it ends', () => {
  assert.equal(paceLine(pace('behind'), true), 'on pace');
  assert.equal(paceLine(pace('ahead'), true), 'ahead');
  assert.match(paceLine(pace('over', '2026-10-27T09:00:00Z'), true)!, /^over · until \S/);
  assert.equal(paceLine(pace('over'), true), 'over');
});
