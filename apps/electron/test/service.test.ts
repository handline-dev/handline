import { test } from 'node:test';
import assert from 'node:assert/strict';
import { currentHl, ensureService, parseEnsure, stopService } from '#src/service.ts';

test('the ensure line is read; a line that is not ours is a failure that quotes it', () => {
  assert.deepEqual(parseEnsure('{"state":"upgraded","version":"abc","reason":"from old"}\n'), {
    state: 'upgraded',
    version: 'abc',
    reason: 'from old',
  });
  assert.deepEqual(parseEnsure('noise\n{"state":"running","version":"v1","reason":""}'), { state: 'running', version: 'v1', reason: '' });
  assert.equal(parseEnsure('{"state":"weird"}').state, 'failed');
  assert.match(parseEnsure('oops').reason, /service ensure said: oops/);
  assert.match(parseEnsure('').reason, /printed nothing/);
});

test('ensure runs the carried handlined; a command that printed nothing and failed is a failure with its message', async () => {
  const seen: string[][] = [];
  const ok = await ensureService('/App/bin/handlined', async (exe, args) => {
    seen.push([exe, ...args]);
    return { out: '{"state":"started","version":"v2","reason":"the job was not loaded"}\n' };
  });
  assert.deepEqual(seen, [['/App/bin/handlined', 'service', 'ensure']]);
  assert.equal(ok.state, 'started');
  const bad = await ensureService('/x', async () => ({ out: '', error: 'spawn ENOENT' }));
  assert.deepEqual(bad, { state: 'failed', version: '', reason: 'spawn ENOENT' });
});

test('stop runs service stop and throws the reason when it fails', async () => {
  await stopService('/x', async (_e, a) => (assert.deepEqual(a, ['service', 'stop']), { out: 'ok' }));
  await assert.rejects(
    stopService('/x', async () => ({ out: '', error: 'not live home' })),
    /not live home/,
  );
});

test('the hl link goes through current, not the bundle', () => {
  assert.equal(currentHl('/h'), '/h/Library/Application Support/Handline/bin/current/hl');
});
