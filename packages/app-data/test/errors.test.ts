import { describe, expect, it } from 'vitest';
import { RpcError } from '@handline/client';
import { classify, retryDelay, retryRead, sentence } from '../src/errors.ts';

const rpc = (code: ConstructorParameters<typeof RpcError>[0], message: string, data?: Record<string, unknown>) =>
  new RpcError(code, message, data);

describe('how an error is sorted for a view', () => {
  it('a lost connection says nothing in the view', () => {
    expect(classify(rpc('transport', 'connection lost'))).toEqual({ kind: 'transport' });
    expect(sentence(classify(rpc('transport', 'x')))).toBe('');
  });
  it('a missing thing is the empty state, not an error', () => {
    expect(classify(rpc('not_found', 'no agent named x')).kind).toBe('not_found');
  });
  it("a refusal carries the daemon's own sentence and hint", () => {
    const f = classify(rpc('refused', 'refused', { reason: 'the usage limit is hit', gate: 'wall', hint: 'wait for the reset' }));
    expect(f).toEqual({ kind: 'refused', reason: 'the usage limit is hit', hint: 'wait for the reset' });
    expect(sentence(f)).toBe('the usage limit is hit (wait for the reset)');
  });
  it('gate unsupported and an unknown method are the not-served state', () => {
    expect(classify(rpc('refused', 'refused', { reason: 'r', gate: 'unsupported' })).kind).toBe('not_served');
    expect(classify(rpc('bad_request', 'unknown method', { method: 'chat.messages' })).kind).toBe('not_served');
    expect(classify(rpc('bad_request', 'bad field', { field: 'agent' })).kind).toBe('other');
  });
  it('a busy daemon is asked again after the wait it named, a few times', () => {
    const busy = rpc('daemon_busy', 'busy', { retry_after_ms: 250 });
    expect(classify(busy)).toEqual({ kind: 'busy', retryAfterMs: 250 });
    expect(retryDelay(0, busy)).toBe(250);
    expect(retryRead(0, busy)).toBe(true);
    expect(retryRead(3, busy)).toBe(false);
    expect(retryRead(0, rpc('refused', 'r', { reason: 'r', gate: 'role' }))).toBe(false);
  });
  it("an old client is the whole app's sentence", () => {
    expect(classify(rpc('protocol', 'old', { reason: 'run hl service install' }))).toEqual({
      kind: 'protocol',
      message: 'run hl service install',
    });
  });
  it('anything else is an error block with Retry', () => {
    expect(classify(rpc('internal', 'boom')).kind).toBe('other');
    expect(classify(new Error('x'))).toEqual({ kind: 'other', message: 'x' });
  });
});
