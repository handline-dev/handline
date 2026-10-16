import assert from 'node:assert/strict';
import { test } from 'node:test';
import { branchCandidates, findNames } from '../src/links.ts';

test('words that look like a branch name, once each, in order', () => {
  assert.deepEqual(branchCandidates('Proposal on branch pr-review-loop-review @2728429a0, file docs/pr-review-loop.md; also story/4153 and pr-review-loop-review'), [
    'pr-review-loop-review',
    'docs/pr-review-loop.md',
    'story/4153',
  ]);
  assert.deepEqual(branchCandidates('main 2026-10-16 a/ b//c plain'), []);
});

test('an agent name is found bare or dotted, as a whole word, not inside a path, file name or longer name', () => {
  const names = ['alpha', 'handline.pc', 'handline.pc.worker2', 'gamma'];
  const at = (t: string) => findNames(t, names).map((s) => t.slice(s.start, s.end));
  assert.deepEqual(at('alpha and gamma, handline.pc.worker2 (handline.pc).'), ['alpha', 'gamma', 'handline.pc.worker2', 'handline.pc']);
  assert.deepEqual(at('alpha.md ./alpha docs/alpha alphabet alpha-2 x@alpha'), []);
  assert.deepEqual(at('ask alpha. then gamma?'), ['alpha', 'gamma']);
});
