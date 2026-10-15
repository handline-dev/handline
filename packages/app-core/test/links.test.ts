import assert from 'node:assert/strict';
import { test } from 'node:test';
import { branchCandidates } from '../src/links.ts';

test('words that look like a branch name, once each, in order', () => {
  assert.deepEqual(branchCandidates('Proposal on branch pr-review-loop-review @2728429a0, file docs/pr-review-loop.md; also story/4153 and pr-review-loop-review'), [
    'pr-review-loop-review',
    'docs/pr-review-loop.md',
    'story/4153',
  ]);
  assert.deepEqual(branchCandidates('main 2026-10-16 a/ b//c plain'), []);
});
