import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { axisTicks, guessLine, lostResets, weekAccounts } from '#src/outlook.ts';

const p = (at: string, left_pct: number | null) => ({ at, left_pct });

describe('guessLine', () => {
  it('starts at the last recorded point so the guess joins the recorded line', () => {
    const past = [p('2026-10-26T10:00:00Z', 80), p('2026-10-26T11:00:00Z', 70)];
    const future = [p('2026-10-26T11:30:00Z', 66), p('2026-10-26T12:30:00Z', 60)];
    assert.deepEqual(guessLine({ past, future }), [past[1], ...future]);
  });
  it('skips a last point with no reading and drops a guess that is not after the join', () => {
    const past = [p('2026-10-26T10:00:00Z', 80), p('2026-10-26T11:00:00Z', null)];
    const future = [p('2026-10-26T09:00:00Z', 90), p('2026-10-26T11:30:00Z', 66)];
    assert.deepEqual(guessLine({ past, future }), [past[0], future[1]]);
  });
  it('is the guess alone when nothing was recorded', () => {
    const future = [p('2026-10-26T11:30:00Z', 66)];
    assert.deepEqual(guessLine({ past: [], future }), future);
  });
});

const w = (at: string, week_left: number | null) => ({ at, five_left: null, week_left, line_left: null });
const by = {
  account: 'alpha',
  past: [w('2026-10-26T10:00:00Z', 60), w('2026-10-26T11:00:00Z', 55)],
  future: [w('2026-10-26T12:00:00Z', 50), w('2026-10-26T14:00:00Z', 40), w('2026-10-26T16:00:00Z', 30)],
  resets: [
    { at: '2026-10-25T00:00:00Z', window: '7d' as const, unused_pct: 18 },
    { at: '2026-10-25T05:00:00Z', window: '5h' as const },
    { at: '2026-10-26T15:00:00Z', window: '7d' as const },
  ],
};
const o = { now: '2026-10-26T11:00:00Z', by_account: [by] };

describe('weekAccounts', () => {
  it('gives the week left now, the next weekly reset and the guess just before it', () => {
    assert.deepEqual(weekAccounts(o), [{ account: 'alpha', leftPct: 55, resetAt: '2026-10-26T15:00:00Z', lostPct: 40 }]);
  });
  it('knows no loss when the guess stops before the reset, and nothing without by_account', () => {
    const short = { ...by, future: by.future.slice(0, 2) };
    assert.equal(weekAccounts({ now: o.now, by_account: [short] })[0]!.lostPct, null);
    assert.deepEqual(weekAccounts({ now: o.now }), []);
  });
});
describe('lostResets', () => {
  it('lists the recorded weekly resets that carry an unused share', () => {
    assert.deepEqual(lostResets(o), [{ account: 'alpha', at: '2026-10-25T00:00:00Z', unusedPct: 18 }]);
  });
});
describe('axisTicks', () => {
  it('is a weekday per local midnight over days, and a clock every 6 hours over a day', () => {
    const t0 = new Date(2026, 9, 26, 10).getTime();
    const days = axisTicks(t0, t0 + 6 * 86_400_000);
    assert.equal(days.length, 6);
    assert.ok(days.every((t) => new Date(t.at).getHours() === 0));
    const hours = axisTicks(t0, t0 + 24 * 3_600_000);
    assert.deepEqual(
      hours.map((t) => new Date(t.at).getHours()),
      [12, 18, 0, 6],
    );
  });
  it('is empty for an empty span', () => assert.deepEqual(axisTicks(5, 5), []));
});
