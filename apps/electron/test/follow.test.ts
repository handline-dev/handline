import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { decide, Follower, watchMain, type Main } from '../src/follow.ts';
import { popoverHtml } from '../src/popover-html.ts';
import { followItems } from '../src/tray.ts';

test('a web-only change rebuilds the page and nothing of the app; an app change rebuilds it too', () => {
  assert.deepEqual(decide(['apps/web/src/App.tsx', 'docs/x.md', 'tests/a.py']), {
    install: false,
    gateway: false,
    web: true,
    electron: false,
  });
  assert.deepEqual(decide(['packages/app-data/src/writes.ts']), { install: false, gateway: false, web: true, electron: false });
  assert.deepEqual(decide(['apps/electron/src/main.ts']), { install: false, gateway: false, web: true, electron: true });
  assert.equal(decide(['packages/client/src/client.ts']).electron, true); // the app bundles it
  assert.deepEqual(decide(['pnpm-lock.yaml']), { install: true, gateway: false, web: true, electron: true });
  assert.deepEqual(decide(['go/cmd/handlined/main.go', 'handline/daemon/x.py']), {
    install: false,
    gateway: true,
    web: false,
    electron: false,
  });
  assert.deepEqual(decide(['docs/a.md']), { install: false, gateway: false, web: false, electron: false });
});

function rig(files: string[]) {
  const main: Main = { sha: 'aaa1111', title: 'first' };
  const calls: string[] = [];
  const ran: string[] = [];
  const f = new Follower({
    run: async (file, args) => {
      ran.push(`${file} ${args.join(' ')}`);
      if (args[0] === 'rev-parse') return 'aaa1111\n';
      if (args[0] === 'diff') return files.join('\n');
      return '';
    },
    peek: async () => main,
    beforeRelaunch: () => calls.push('save'),
    relaunch: () => calls.push('relaunch'),
  });
  const fresh: boolean[] = [];
  f.onChange((x) => fresh.push(x));
  return { f, main, calls, ran, fresh };
}

test('main moving is offered once; Later keeps it quiet until main moves again', async () => {
  const r = rig([]);
  await r.f.init();
  assert.equal(r.f.state, 'current');
  r.main.sha = 'bbb2222';
  await r.f.look();
  assert.equal(r.f.state, 'available');
  assert.equal(r.fresh.at(-1), true);
  await r.f.look(); // the same main again is not news
  assert.equal(r.fresh.at(-1), false);
  r.f.later();
  await r.f.look();
  assert.equal(r.fresh.at(-1), false); // declined: no popover
  assert.equal(r.f.state, 'available'); // the tray still offers it
  r.main.sha = 'ccc3333';
  await r.f.look();
  assert.equal(r.fresh.at(-1), true);
  r.main.sha = 'aaa1111'; // main went back to what the app runs
  await r.f.look();
  assert.equal(r.f.state, 'current');
  assert.equal(r.f.next, null);
});

test('Restart now fetches, moves the checkout to that main, builds only what changed and relaunches after saving the page', async () => {
  const r = rig(['apps/web/src/App.tsx']);
  await r.f.init();
  r.main.sha = 'bbb2222';
  await r.f.look();
  await r.f.restartNow();
  assert.deepEqual(
    r.ran.filter((c) => !c.startsWith('git rev-parse')),
    ['git fetch origin main', 'git diff --name-only aaa1111 bbb2222', 'git merge --ff-only bbb2222', 'pnpm --filter @handline/web build'],
  );
  assert.deepEqual(r.calls, ['save', 'relaunch']);

  const e = rig(['apps/electron/src/main.ts']);
  await e.f.init();
  e.main.sha = 'ddd4444';
  await e.f.restartNow();
  assert.ok(e.ran.includes('pnpm --filter @handline/electron build'));
});

test('a failed build is shown, nothing relaunches, and the next look tries again', async () => {
  const calls: string[] = [];
  const f = new Follower({
    run: async (file, args) => {
      if (file === 'pnpm') throw new Error('pnpm build: vite failed');
      return args[0] === 'diff' ? 'apps/web/a.ts' : 'aaa1111';
    },
    peek: async () => ({ sha: 'bbb2222', title: 't' }),
    beforeRelaunch: () => calls.push('save'),
    relaunch: () => calls.push('relaunch'),
  });
  await f.init();
  await f.restartNow();
  assert.equal(f.state, 'failed');
  assert.equal(f.reason, 'pnpm build: vite failed');
  assert.deepEqual(calls, []);
  await f.look();
  assert.equal(f.state, 'available');
});

test('the popover says the new sha and title, and a title cannot add markup', () => {
  const html = popoverHtml({ sha: 'bbb2222ffff', title: '<img src=x onerror=alert(1)> & "q"' }, 'N0NCE');
  assert.match(html, /bbb2222/);
  assert.ok(!html.includes('<img'));
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt; &amp; &quot;q&quot;/);
  assert.match(html, /Restart now/);
  assert.match(html, /Later/);
  // The buttons are wired by one inline script, which the policy lets run only with this page's nonce.
  assert.match(html, /script-src 'nonce-N0NCE'/);
  assert.match(html, /<script nonce="N0NCE">/);
});

test('the tray says what the app is on, and offers the restart once main has moved', () => {
  const base = { sha: 'aaa1111ffff', next: null, reason: '', check: () => undefined, restart: () => undefined };
  assert.equal(followItems({ ...base, state: 'current' })[0]!.label, 'Handline is on main aaa1111');
  const moved = followItems({ ...base, state: 'available', next: 'bbb2222ffff' });
  assert.equal(moved[0]!.label, 'Handline is on main aaa1111; main is now bbb2222');
  assert.equal(moved[1]!.label, 'Restart to update');
  assert.match(followItems({ ...base, state: 'failed', reason: 'vite failed' })[0]!.label!, /vite failed/);
});

test('watchMain calls back once when the main ref is replaced or packed-refs is rewritten, and not for another branch', async (t) => {
  const git = fs.mkdtempSync(path.join(os.tmpdir(), 'watch-'));
  fs.mkdirSync(path.join(git, 'refs', 'heads'), { recursive: true });
  let n = 0;
  const errors: Error[] = [];
  const stop = watchMain(
    git,
    () => n++,
    (e) => errors.push(e),
    50,
  );
  await new Promise((r) => setTimeout(r, 100));
  if (errors.length) return t.skip(`fs.watch is not available here: ${errors[0]!.message}`);
  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const until = async (f: () => boolean) => {
    for (let i = 0; i < 60 && !f(); i++) await wait(50);
  };
  fs.writeFileSync(path.join(git, 'refs', 'heads', 'other'), 'x');
  await wait(300);
  assert.equal(n, 0);
  fs.writeFileSync(path.join(git, 'refs', 'heads', 'main.lock'), 'x');
  fs.renameSync(path.join(git, 'refs', 'heads', 'main.lock'), path.join(git, 'refs', 'heads', 'main'));
  await wait(300);
  await until(() => n >= 1);
  assert.equal(n, 1);
  fs.writeFileSync(path.join(git, 'packed-refs'), 'x');
  await wait(300);
  await until(() => n >= 2);
  assert.equal(n, 2);
  stop();
});

test('a watch that cannot start or breaks is reported, not thrown', async () => {
  const errors: string[] = [];
  const stop = watchMain(
    path.join(os.tmpdir(), `no-such-${Date.now()}`),
    () => undefined,
    (e) => errors.push(e.message),
  );
  stop();
  assert.equal(errors.length, 2); // refs/heads and the folder itself

  const f = new Follower({
    run: async () => '',
    peek: async () => ({ sha: 'x', title: '' }),
    beforeRelaunch: () => undefined,
    relaunch: () => undefined,
  });
  f.watchFailed(new Error('EMFILE'));
  assert.equal(f.state, 'failed');
  assert.equal(f.reason, 'cannot watch main: EMFILE');
});
