import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Sender } from '#src/bridge.ts';
import { isAttach, OpenIn, quote, type OpenInDeps } from '#src/open-in.ts';

const ORIGIN = 'http://127.0.0.1:5000';
const top: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: null } };
const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'hl-openin-')));
after(() => fs.rmSync(root, { recursive: true, force: true }));
const wt = path.join(root, '-wt'); // a leading dash: it must come after `--`
const file = path.join(root, 'file.txt');
fs.mkdirSync(wt);
fs.writeFileSync(file, 'x');
const ATTACH = ['tmux', '-L', 'ocp', 'attach-session', '-t', 'ocp'];
const code = (r: { ok: boolean }) => (r.ok ? 'ok' : (r as unknown as { code: string }).code);

const present = new Set<string>();
function make(over: Partial<OpenInDeps> = {}) {
  const calls: { file: string; args: string[] }[] = [];
  const timers: (() => void)[] = [];
  const o = new OpenIn(ORIGIN, {
    platform: 'darwin',
    home: root,
    tmp: root,
    worktreeOf: async (a) =>
      a === 'handline.x' ? wt : a === 'handline.file' ? file : a === 'handline.gone' ? path.join(root, 'gone') : null,
    attachOf: async () => ATTACH,
    exec: async (f, args) => void calls.push({ file: f, args }),
    exists: async (p) => present.has(p),
    later: (fn) => void timers.push(fn),
    ...over,
  });
  return { o, calls, timers };
}
const VS = '/Applications/Visual Studio Code.app';
const TERM = '/System/Applications/Utilities/Terminal.app';
const launchers = () => fs.readdirSync(root).filter((n) => n.startsWith('handline-'));

test('a call from another frame, or a page that is not the window, does nothing', async () => {
  const { o, calls } = make();
  const other: Sender = { senderFrame: { url: 'https://evil.example/', parent: null } };
  assert.equal(code(await o.open(other, { app: 'terminal', agent: 'handline.x' })), 'bad_request');
  assert.equal(code(await o.apps(other)), 'bad_request');
  assert.deepEqual(calls, []);
});

test('only { app, agent } with an app of the three and a plain agent name; no path, argv or app name', async () => {
  const { o, calls } = make();
  const bad: unknown[] = [
    null,
    [],
    'terminal',
    { app: 'terminal' },
    { agent: 'handline.x' },
    { app: 'terminal', agent: 'handline.x', path: '/etc' },
    { app: 'terminal', agent: 'handline.x', argv: ['rm'] },
    { app: 'Safari', agent: 'handline.x' },
    { app: '/Applications/Evil.app', agent: 'handline.x' },
    { app: ['terminal'], agent: 'handline.x' },
    { app: 'terminal', agent: '../x' },
    { app: 'terminal', agent: '-x; rm' },
    { app: 'terminal', agent: 'a'.repeat(513) },
    { app: 'terminal', agent: '' },
    { app: 'terminal', agent: 5 },
  ];
  for (const b of bad) assert.equal(code(await o.open(top, b)), 'bad_request', JSON.stringify(b));
  assert.deepEqual(calls, []);
});

test('off macOS it opens nothing and lists no app', async () => {
  const { o, calls } = make({ platform: 'linux' });
  assert.equal(code(await o.open(top, { app: 'terminal', agent: 'handline.x' })), 'unavailable');
  assert.deepEqual(await o.apps(top), { ok: true, apps: [] });
  assert.deepEqual(calls, []);
});

test('an agent with no folder, a folder that is gone and a path that is a file are not_found', async () => {
  const { o, calls } = make();
  for (const agent of ['handline.nobody', 'handline.gone', 'handline.file'])
    assert.equal(code(await o.open(top, { app: 'terminal', agent })), 'not_found', agent);
  const thrown = make({
    worktreeOf: async () => {
      throw new Error('daemon down');
    },
  });
  assert.equal(code(await thrown.o.open(top, { app: 'terminal', agent: 'handline.x' })), 'not_found');
  assert.deepEqual([...calls, ...thrown.calls], []);
});

test('an app that is not installed is unavailable and runs nothing', async () => {
  const { o, calls } = make();
  const r = await o.open(top, { app: 'vscode', agent: 'handline.x' });
  assert.equal(code(r), 'unavailable');
  assert.deepEqual(calls, []);
});

test('VS Code opens the real folder after `--`, by /usr/bin/open with an argument array', async () => {
  present.add(VS);
  const { o, calls } = make();
  const link = path.join(root, 'link');
  fs.symlinkSync(wt, link);
  const viaLink = make({ worktreeOf: async () => link });
  assert.deepEqual(await o.open(top, { app: 'vscode', agent: 'handline.x' }), { ok: true });
  assert.deepEqual(await viaLink.o.open(top, { app: 'vscode', agent: 'handline.x' }), { ok: true });
  assert.deepEqual(calls, [{ file: '/usr/bin/open', args: ['-a', 'Visual Studio Code', '--', wt] }]);
  assert.deepEqual(viaLink.calls, calls);
});

test('a failing open is unavailable with its message', async () => {
  present.add(VS);
  const { o } = make({
    exec: async () => {
      throw new Error('open failed');
    },
  });
  const r = await o.open(top, { app: 'vscode', agent: 'handline.x' });
  assert.equal(code(r), 'unavailable');
  assert.match((r as { message: string }).message, /open failed/);
});

test('the apps list holds what is installed: Terminal brings Claude Code with it', async () => {
  present.clear();
  assert.deepEqual(await make().o.apps(top), { ok: true, apps: [] });
  present.add(VS);
  assert.deepEqual(await make().o.apps(top), { ok: true, apps: ['vscode'] });
  present.add(TERM);
  assert.deepEqual(await make().o.apps(top), { ok: true, apps: ['vscode', 'terminal', 'claude'] });
});

test('Terminal opens the real folder after `--`', async () => {
  present.add(TERM);
  const { o, calls } = make();
  assert.deepEqual(await o.open(top, { app: 'terminal', agent: 'handline.x' }), { ok: true });
  assert.deepEqual(calls, [{ file: '/usr/bin/open', args: ['-a', 'Terminal', '--', wt] }]);
});

test('Claude Code writes a 0700 launcher in a folder of its own, quotes each word, opens it in Terminal and removes the folder later', async () => {
  present.add(TERM);
  const { o, calls, timers } = make();
  const r = await o.open(top, { app: 'claude', agent: 'handline.x' });
  assert.deepEqual(r, { ok: true });
  const dir = launchers()[0]!;
  const launcher = path.join(root, dir, 'handline-claude.command');
  assert.equal(fs.readFileSync(launcher, 'utf8'), "#!/bin/sh\nexec 'tmux' '-L' 'ocp' 'attach-session' '-t' 'ocp'\n");
  assert.equal(fs.statSync(launcher).mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(root, dir)).mode & 0o777, 0o700);
  assert.deepEqual(calls, [{ file: '/usr/bin/open', args: ['-a', 'Terminal', launcher] }]);
  assert.equal(timers.length, 1);
  timers[0]!();
  await new Promise((r) => setTimeout(r, 50));
  assert.deepEqual(launchers(), []);
});

test('a command that is not the tmux attach shape writes nothing and is unavailable', async () => {
  const shapes: (string[] | null)[] = [
    null,
    [],
    ['sh', '-c', 'rm -rf ~'],
    ['/usr/bin/tmux', '-L', 'ocp', 'attach-session', '-t', 'ocp'],
    ['tmux', '-L', 'ocp', '-C', 'attach-session', '-t', 'ocp'],
    ['tmux', '-L', '../x', 'attach-session', '-t', 'ocp'],
    ['tmux', '-L', 'ocp; rm', 'attach-session', '-t', 'ocp'],
    ['tmux', '-L', 'ocp', 'attach-session', '-t', "o'cp"],
    ['tmux', '-L', 'ocp', 'attach-session', '-t', 'ocp', 'extra'],
    ['tmux', '-L', 'ocp', 'kill-server', '-t', 'ocp'],
  ];
  present.add(TERM);
  for (const argv of shapes) {
    const { o, calls } = make({ attachOf: async () => argv });
    assert.equal(code(await o.open(top, { app: 'claude', agent: 'handline.x' })), 'unavailable', JSON.stringify(argv));
    assert.deepEqual(calls, []);
    assert.deepEqual(launchers(), []);
  }
  present.add(TERM);
  const thrown = make({
    attachOf: async () => {
      throw new Error('no terminal');
    },
  });
  assert.equal(code(await thrown.o.open(top, { app: 'claude', agent: 'handline.x' })), 'unavailable');
  assert.deepEqual(launchers(), []);
});

test('a failed Terminal open removes the launcher folder at once', async () => {
  present.add(TERM);
  const { o } = make({
    exec: async () => {
      throw new Error('no');
    },
  });
  const r = await o.open(top, { app: 'claude', agent: 'handline.x' });
  assert.equal(r.ok, false);
  await new Promise((r) => setTimeout(r, 50));
  assert.deepEqual(launchers(), []);
});

test('quote closes, escapes and reopens a quote, and isAttach takes only the one shape', () => {
  assert.equal(quote("a'b"), `'a'\\''b'`);
  assert.equal(quote('$(x) `y` ;'), "'$(x) `y` ;'");
  assert.equal(isAttach(ATTACH), true);
  assert.equal(isAttach(['tmux', '-L', 'ocp-1a2b3c4d', 'attach-session', '-t', 'ocp']), true);
});
