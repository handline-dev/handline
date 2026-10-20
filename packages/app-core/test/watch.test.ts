import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isGate, watchLine } from '../src/index.ts';

test('a gate task is owned by gate and shows its label, a link where there is one, and the result once it has one', () => {
  assert.equal(isGate({ to: 'gate' }), true);
  assert.equal(isGate({ to: 'handline.pc' }), false);
  const watch = { vendor: 'github', target: 'acme/app#412', until: 'ci' as const, label: 'PR #412 CI', result: null };
  assert.equal(watchLine({ watch: undefined }), null);
  assert.deepEqual(watchLine({ watch }), { url: 'https://github.com/acme/app/pull/412', text: 'PR #412 CI', result: null });
  assert.equal(watchLine({ watch: { ...watch, until: 'closed', result: 'timed_out' } })?.result, 'timed out');
  assert.equal(watchLine({ watch: { ...watch, until: 'done', label: 'Issue #7 done', target: 'acme/app#7', result: 'passed' } })?.url, 'https://github.com/acme/app/issues/7');
  assert.equal(watchLine({ watch: { ...watch, vendor: 'jira', target: 'X-1' } })?.url, null);
});
