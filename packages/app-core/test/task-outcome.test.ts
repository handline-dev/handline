import assert from 'node:assert/strict';
import { test } from 'node:test';
import { taskOutcome } from '#src/index.ts';

test('an audit says PASS first and the head after, or PR and head first and PASS after', () => {
  assert.deepEqual(taskOutcome('PASS @8488680c0 matches PC', 'Re-check PR 893 This computer'), {
    verdict: 'PASS',
    pr: 893,
    sha: '8488680c0',
    live: true,
    rest: 'matches PC',
  });
  assert.deepEqual(taskOutcome('PASS: PR #7 @abcdef1 fine', 'Audit PR 7'), {
    verdict: 'PASS',
    pr: 7,
    sha: 'abcdef1',
    live: false,
    rest: 'fine',
  });
  assert.equal(taskOutcome('PR #7 @abcdef1 PASS', null).verdict, 'PASS');
  assert.equal(taskOutcome('PASS on PR #7 @abcdef1', null).verdict, 'PASS');
});

test('a ux live check names the PR with on #n and is a live check', () => {
  const o = taskOutcome('UX live PASS on #896 @0c520d18d live menu bar', 'Live-check PR 896');
  assert.deepEqual(o, { verdict: 'PASS', pr: 896, sha: '0c520d18d', live: true, rest: 'live menu bar' });
  assert.equal(taskOutcome('Web half PASS @0c520d1 shots', null).live, true);
});

test('FAIL has the same shapes', () => {
  assert.equal(taskOutcome('FAIL @3c1f9a2 drop opens nothing', 'Live-check PR 880: split drag').verdict, 'FAIL');
  assert.equal(taskOutcome('PR #880 @3c1f9a2 FAIL', null).verdict, 'FAIL');
});

test('anything else has no verdict, as merge_ready.py does not take it as a pass', () => {
  assert.equal(taskOutcome('NOT PASS @abcdef1', null).verdict, null);
  assert.equal(taskOutcome('Findings, no PASS @abcdef1', null).verdict, null);
  assert.equal(taskOutcome('PASS but no head', null).verdict, null);
  assert.deepEqual(taskOutcome('Closed 6, kept 5; list in the task.', 'Triage of the 11 handline tasks'), {
    verdict: null,
    pr: null,
    sha: null,
    live: false,
    rest: 'Closed 6, kept 5; list in the task.',
  });
  assert.deepEqual(taskOutcome(null, null), { verdict: null, pr: null, sha: null, live: false, rest: '' });
});
