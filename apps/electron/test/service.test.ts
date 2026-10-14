import assert from 'node:assert/strict';
import test from 'node:test';
import { ensureService, stopService, type ServiceDeps } from '../src/service.ts';

function deps(o: Partial<ServiceDeps> & { upAfter?: number; replies?: { code: number; out: string }[] } = {}) {
  const calls: string[][] = [];
  let polls = 0;
  const replies = [...(o.replies ?? [])];
  const d: ServiceDeps = {
    launchctl: async (args) => (calls.push(args), replies.shift() ?? { code: 0, out: '' }),
    up: async () => polls++ >= (o.upAfter ?? Infinity),
    installed: () => true,
    domain: 'gui/501',
    plist: '/p/dev.orcapipe.daemon.plist',
    wait: async () => undefined,
    polls: 5,
    ...o,
  };
  return { d, calls };
}

test('a running daemon is attached to and nothing is started', async () => {
  const { d, calls } = deps({ upAfter: 0 });
  assert.deepEqual(await ensureService(d), { ok: true, started: false });
  assert.deepEqual(calls, []);
});

test('a stopped service is kickstarted, not spawned, and waited for', async () => {
  const { d, calls } = deps({ upAfter: 3 });
  assert.deepEqual(await ensureService(d), { ok: true, started: true });
  assert.deepEqual(calls, [['kickstart', 'gui/501/dev.orcapipe.daemon']]);
});

test('a service launchd has not loaded is bootstrapped from its plist, then kickstarted', async () => {
  const { d, calls } = deps({ upAfter: 3, replies: [{ code: 113, out: 'Could not find service' }] });
  assert.deepEqual(await ensureService(d), { ok: true, started: true });
  assert.deepEqual(calls, [
    ['kickstart', 'gui/501/dev.orcapipe.daemon'],
    ['bootstrap', 'gui/501', '/p/dev.orcapipe.daemon.plist'],
    ['kickstart', 'gui/501/dev.orcapipe.daemon'],
  ]);
});

test('no plist, a refusal and a silent service each give a reason and start nothing else', async () => {
  const none = deps({ installed: () => false });
  const r1 = await ensureService(none.d);
  assert.equal(r1.ok === false && /not installed/.test(r1.reason), true);
  assert.deepEqual(none.calls, []);
  const refused = deps({ replies: [{ code: 5, out: 'Input/output error' }] });
  const r2 = await ensureService(refused.d);
  assert.equal(r2.ok === false && /would not start.*Input\/output/.test(r2.reason), true);
  const silent = deps();
  const r3 = await ensureService(silent.d);
  assert.equal(r3.ok === false && /did not answer/.test(r3.reason), true);
});

test('stopping boots the service out, and a service that is not there is already stopped', async () => {
  const a = deps();
  assert.deepEqual(await stopService(a.d), { ok: true });
  assert.deepEqual(a.calls, [['bootout', 'gui/501/dev.orcapipe.daemon']]);
  const b = deps({ replies: [{ code: 3, out: 'Boot-out failed: 3: No such process' }] });
  assert.deepEqual(await stopService(b.d), { ok: true });
});
