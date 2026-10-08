import { test } from 'node:test';
import assert from 'node:assert/strict';
import { grant } from '../src/grant.ts';

test('a daemon that failed to start goes to the page as the reason, with the own host still named', () => {
  assert.deepEqual(grant(null, '100.1.2.3', { state: 'failed', reason: 'the gateway exited at start (exit 1)' }), {
    token: null,
    ownHost: '100.1.2.3',
    failure: 'the gateway exited at start (exit 1)',
  });
});

test('a failure with no reason still says that nothing started; a running or stopped daemon is no failure', () => {
  assert.equal(grant(null, null, { state: 'failed', reason: '' }).failure, 'the daemon did not start');
  assert.equal(grant('t', null, { state: 'running', reason: '' }).failure, null);
  assert.equal(grant('t', null, { state: 'stopped', reason: '' }).failure, null);
});
