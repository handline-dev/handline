import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { decide, Follower, peekMain, watchMain, type Main } from '#src/follow.ts';
import { trayMenu } from '#src/tray.ts';

test('a web-only change rebuilds the page and nothing of the app; an app change rebuilds it too', () => {
  assert.deepEqual(decide(['apps/web/src/App.tsx', 'docs/x.md', 'tests/a.py']), {
    install: false,
    web: true,
    electron: false,
  });
  assert.deepEqual(decide(['packages/app-data/src/writes.ts']), { install: false, web: true, electron: false });
  assert.deepEqual(decide(['apps/electron/src/main.ts']), { install: false, web: true, electron: true });
  assert.equal(decide(['packages/client/src/client.ts']).electron, true); // the app bundles it
  assert.deepEqual(decide(['pnpm-lock.yaml']), { install: true, web: true, electron: true });
  assert.deepEqual(decide(['go/cmd/handlined/main.go', 'handline/daemon/x.py']), { install: false, web: false, electron: false });
  assert.deepEqual(decide(['docs/a.md']), { install: false, web: false, electron: false });
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
    upgradeDaemon: async (sha) => void ran.push(`upgrade ${sha}`),
    clearWeb: () => calls.push('clear'),
    swapWeb: () => calls.push('swap'),
    beforeRelaunch: () => calls.push('save'),
    relaunch: () => calls.push('relaunch'),
  });
  const fresh: boolean[] = [];
  f.onChange((x) => fresh.push(x));
  return { f, main, calls, ran, fresh };
}

test('main moving is news once; a main that moved again is news again, and one that went back is nothing', async () => {
  const r = rig([]);
  await r.f.init();
  assert.equal(r.f.state, 'current');
  r.main.sha = 'bbb2222';
  await r.f.look();
  assert.equal(r.f.state, 'available');
  assert.equal(r.fresh.at(-1), true);
  await r.f.look(); // the same main again is not news
  assert.equal(r.fresh.at(-1), false);
  r.main.sha = 'ccc3333';
  await r.f.look();
  assert.equal(r.fresh.at(-1), true);
  r.main.sha = 'aaa1111'; // main went back to what the app runs
  await r.f.look();
  assert.equal(r.f.state, 'current');
  assert.equal(r.f.next, null);
});

test('prepare fetches, moves the checkout to that main and builds the page beside the running one, then waits; nothing restarts', async () => {
  const r = rig(['apps/web/src/App.tsx']);
  await r.f.init();
  r.main.sha = 'bbb2222';
  await r.f.look();
  const steps: string[] = [];
  r.f.onChange(() => r.f.state === 'updating' && steps.push(`${r.f.step} ${Math.round(r.f.fraction * 100)}`));
  await r.f.prepare();
  assert.equal(r.f.state, 'ready');
  assert.deepEqual(
    r.ran.filter((c) => !c.startsWith('git rev-parse')),
    [
      'git fetch origin +refs/remotes/origin/main:refs/remotes/origin/main',
      'git diff --name-only aaa1111 bbb2222',
      'git merge --ff-only bbb2222',
      'pnpm --filter @handline/web build --outDir dist-next',
      'pnpm --filter @handline/electron build',
    ],
  );
  assert.deepEqual(steps, ['Preparing 0', 'Building (1 of 2) 0', 'Building (2 of 2) 50']);
  assert.deepEqual(r.calls, ['clear']);
  await r.f.look(); // a built update is not looked at again
  assert.equal(r.f.state, 'ready');
});

test('Restart now moves the daemon onto the built main, puts the built page in place, saves the page and relaunches', async () => {
  const r = rig(['apps/electron/src/main.ts']);
  await r.f.init();
  r.main.sha = 'ddd4444';
  await r.f.prepare();
  assert.ok(r.ran.includes('pnpm --filter @handline/electron build'));
  await r.f.restartNow();
  assert.deepEqual(r.ran.slice(-1), ['upgrade ddd4444']);
  assert.deepEqual(r.calls, ['clear', 'swap', 'save', 'relaunch']);
});

test('a retry after a failed web build builds the page again, from where the first try began, and clears the half-built one', async () => {
  const ran: string[] = [];
  const calls: string[] = [];
  let head = 'aaa1111'; // the merge moves HEAD to the target, as git does
  let broken = true;
  const f = new Follower({
    run: async (file, args) => {
      ran.push(`${file} ${args.join(' ')}`);
      if (args[0] === 'rev-parse') return `${head}\n`;
      if (args[0] === 'diff') return args.includes('aaa1111') ? 'apps/web/src/App.tsx' : ''; // from the moved HEAD there is nothing to see
      if (args[0] === 'merge') head = 'bbb2222';
      if (file === 'pnpm' && args.includes('@handline/web') && broken) throw new Error('vite failed');
      return '';
    },
    peek: async () => ({ sha: 'bbb2222', title: 't' }),
    upgradeDaemon: async () => undefined,
    clearWeb: () => calls.push('clear'),
    swapWeb: () => calls.push('swap'),
    beforeRelaunch: () => undefined,
    relaunch: () => undefined,
  });
  await f.init();
  await f.prepare();
  assert.equal(f.state, 'failed');
  broken = false;
  ran.length = 0;
  await f.retry();
  assert.equal(f.state, 'ready');
  assert.ok(ran.includes('git diff --name-only aaa1111 bbb2222'));
  assert.ok(ran.includes('pnpm --filter @handline/web build --outDir dist-next'));
  assert.deepEqual(calls, ['clear', 'clear']); // each try starts from no half-built page
});

test('Restart now before anything is built does nothing', async () => {
  const r = rig([]);
  await r.f.init();
  await r.f.restartNow();
  assert.deepEqual(r.calls, []);
});

test('a daemon that does not upgrade stops the restart before the relaunch; its reason is kept and Try again repeats the restart, not the build', async () => {
  const calls: string[] = [];
  let ok = false;
  const ran: string[] = [];
  const f = new Follower({
    run: async (file, args) => (ran.push(`${file} ${args[0]}`), args[0] === 'diff' ? 'go/x.go' : 'aaa1111'),
    peek: async () => ({ sha: 'bbb2222', title: 't' }),
    upgradeDaemon: async () => {
      if (!ok) throw new Error('the daemon did not upgrade: exe prints another sha');
    },
    clearWeb: () => calls.push('clear'),
    swapWeb: () => calls.push('swap'),
    beforeRelaunch: () => calls.push('save'),
    relaunch: () => calls.push('relaunch'),
  });
  await f.init();
  await f.prepare();
  const built = ran.length;
  await f.restartNow();
  assert.equal(f.state, 'failed');
  assert.equal(f.reason, 'the daemon did not upgrade: exe prints another sha');
  assert.deepEqual(calls, []);
  ok = true;
  await f.retry();
  assert.equal(ran.length, built); // nothing was built again
  assert.deepEqual(calls, ['clear', 'swap', 'save', 'relaunch']);
});

test('a failed build is shown with the command output behind it, nothing relaunches, and Try again builds again', async () => {
  const calls: string[] = [];
  let broken = true;
  const f = new Follower({
    run: async (file, args) => {
      if (file === 'pnpm' && broken) throw new Error('pnpm build: vite failed');
      return args[0] === 'diff' ? 'apps/web/a.ts' : 'aaa1111';
    },
    peek: async () => ({ sha: 'bbb2222', title: 't' }),
    upgradeDaemon: async () => undefined,
    clearWeb: () => undefined,
    swapWeb: () => undefined,
    beforeRelaunch: () => calls.push('save'),
    relaunch: () => calls.push('relaunch'),
  });
  await f.init();
  await f.prepare();
  assert.equal(f.state, 'failed');
  assert.equal(f.reason, 'pnpm build: vite failed');
  assert.match(f.log, /\$ pnpm --filter @handline\/web build --outDir dist-next\npnpm build: vite failed/);
  assert.deepEqual(calls, []);
  broken = false;
  await f.retry();
  assert.equal(f.state, 'ready');
});

const actions = {
  open: () => undefined,
  startDaemon: () => undefined,
  quit: () => undefined,
  inbox: () => undefined,
  settings: () => undefined,
  reconnect: () => undefined,
  copy: () => undefined,
};

test('the menu says what the update flow says: updating, ready to restart, not updated; the commit is a detail', () => {
  const base = { sha: 'aaa1111ffff', next: null, reason: '', check: () => undefined, restart: () => undefined };
  const upd = (phase: 'idle' | 'preparing' | 'ready' | 'later' | 'failed', reason = '') => ({
    phase,
    reason,
    restart: () => undefined,
    retry: () => undefined,
  });
  const labels = (update: ReturnType<typeof upd>, details = false) =>
    trayMenu('running', '', actions, { ...base, state: 'current' }, undefined, { unread: 0, details, update }).map((i) => i.label ?? '-');
  assert.ok(!labels(upd('idle')).some((l) => /main|update|restart/i.test(l)));
  assert.ok(labels(upd('idle'), true).includes('Handline is on main aaa1111'));
  assert.ok(labels(upd('preparing')).includes('Updating Handline…'));
  assert.ok(labels(upd('ready')).includes('Handline is ready to restart') && labels(upd('ready')).includes('Restart now'));
  assert.ok(labels(upd('later')).includes('Restart now'));
  assert.ok(
    labels(upd('failed', 'vite failed')).includes('Handline was not updated: vite failed') && labels(upd('failed')).includes('Try again'),
  );
});

// fs.watch can start and then deliver nothing (macOS with an overloaded fseventsd); a bare watch on the same folder tells that from a bug in watchMain.
async function watchDelivers(dir: string): Promise<boolean> {
  let seen = false;
  const w = fs.watch(dir, () => (seen = true));
  await new Promise((r) => setTimeout(r, 100));
  fs.writeFileSync(path.join(dir, 'probe'), 'x');
  for (let i = 0; i < 20 && !seen; i++) await new Promise((r) => setTimeout(r, 50));
  w.close();
  return seen;
}

test('watchMain calls back once when the main ref is replaced or packed-refs is rewritten, and not for another branch', async (t) => {
  const git = fs.mkdtempSync(path.join(os.tmpdir(), 'watch-'));
  fs.mkdirSync(path.join(git, 'refs', 'remotes', 'origin'), { recursive: true });
  let n = 0;
  const errors: Error[] = [];
  const stop = watchMain(
    git,
    () => n++,
    (e) => errors.push(e),
    50,
  );
  t.after(stop);
  t.after(() => fs.rmSync(git, { recursive: true, force: true }));
  await new Promise((r) => setTimeout(r, 100));
  if (errors.length) return t.skip(`fs.watch is not available here: ${errors[0]!.message}`);
  if (!(await watchDelivers(git))) return t.skip('fs.watch delivers no events on this host');
  const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
  const until = async (f: () => boolean) => {
    for (let i = 0; i < 60 && !f(); i++) await wait(50);
  };
  fs.writeFileSync(path.join(git, 'refs', 'remotes', 'origin', 'other'), 'x');
  await wait(300);
  assert.equal(n, 0);
  fs.writeFileSync(path.join(git, 'refs', 'remotes', 'origin', 'main.lock'), 'x');
  fs.renameSync(path.join(git, 'refs', 'remotes', 'origin', 'main.lock'), path.join(git, 'refs', 'remotes', 'origin', 'main'));
  await wait(300);
  await until(() => n >= 1);
  assert.equal(n, 1);
  fs.writeFileSync(path.join(git, 'packed-refs'), 'x');
  await wait(300);
  await until(() => n >= 2);
  assert.equal(n, 2);
});

test('main is GitHub main: the source repo fetches origin main first and reads origin/main, not its own main branch', async () => {
  const ran: string[] = [];
  const git = async (...args: string[]) => {
    ran.push(args.join(' '));
    return args[0] === 'rev-parse' ? 'ccc3333' : 'Merge pull request #1';
  };
  assert.deepEqual(await peekMain(git), { sha: 'ccc3333', title: 'Merge pull request #1' });
  assert.deepEqual(ran, ['fetch origin main', 'rev-parse origin/main', 'log -1 --format=%s origin/main']);
});

test('offline, the last fetched origin/main still answers', async () => {
  const git = async (...args: string[]) => {
    if (args[0] === 'fetch') throw new Error('git fetch: could not resolve host');
    return args[0] === 'rev-parse' ? 'ddd4444' : 't';
  };
  assert.equal((await peekMain(git)).sha, 'ddd4444');
});

test('a watch that cannot start or breaks is reported, not thrown', async () => {
  const errors: string[] = [];
  const stop = watchMain(
    path.join(os.tmpdir(), `no-such-${Date.now()}`),
    () => undefined,
    (e) => errors.push(e.message),
  );
  stop();
  assert.equal(errors.length, 2); // refs/remotes/origin and the folder itself

  const f = new Follower({
    run: async () => '',
    peek: async () => ({ sha: 'x', title: '' }),
    upgradeDaemon: async () => undefined,
    clearWeb: () => undefined,
    swapWeb: () => undefined,
    beforeRelaunch: () => undefined,
    relaunch: () => undefined,
  });
  f.watchFailed(new Error('EMFILE'));
  assert.equal(f.state, 'failed');
  assert.equal(f.reason, 'cannot watch main: EMFILE');
});
