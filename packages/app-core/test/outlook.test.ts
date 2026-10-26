import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { guessLine } from '#src/outlook.ts';

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
