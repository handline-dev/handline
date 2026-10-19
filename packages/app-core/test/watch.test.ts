import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isGate, watchLine } from '../src/index.ts';

test('a gate task is owned by gate and names its pull request, with the result once it has one', () => {
  assert.equal(isGate({ to: 'gate' }), true);
  assert.equal(isGate({ to: 'handline.pc' }), false);
  const watch = { repo: 'acme/app', pr: 412, on: 'ci' as const, result: null };
  assert.equal(watchLine({ watch: undefined }), null);
  assert.deepEqual(watchLine({ watch }), { url: 'https://github.com/acme/app/pull/412', text: 'acme/app#412 CI', result: null });
  assert.equal(watchLine({ watch: { ...watch, on: 'close', result: 'timed_out' } })?.result, 'timed out');
});
