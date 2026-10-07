import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Sender } from '../src/bridge.ts';
import { Opener } from '../src/open.ts';

const ORIGIN = 'http://127.0.0.1:5000';
const top: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: null } };

const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'hl-open-')));
const wt = path.join(root, 'wt');
const home = path.join(root, 'home');
fs.mkdirSync(path.join(wt, 'docs'), { recursive: true });
fs.mkdirSync(path.join(wt, 'x.app'));
fs.mkdirSync(path.join(wt, 'thing.bundle'));
fs.mkdirSync(path.join(wt, 'plain'));
fs.mkdirSync(home);
fs.writeFileSync(path.join(wt, 'docs', 'a.md'), 'x');
fs.writeFileSync(path.join(wt, 'RUN.SH'), 'x');
fs.writeFileSync(path.join(wt, 'tool'), 'x', { mode: 0o755 });
fs.writeFileSync(path.join(home, 'n.txt'), 'x');
fs.writeFileSync(path.join(root, 'outside.md'), 'x');
fs.symlinkSync('/bin/sh', path.join(wt, 'link-to-sh'));
fs.symlinkSync(path.join(root, 'outside.md'), path.join(wt, 'link-out.md'));

function make() {
  const log: string[] = [];
  const o = new Opener(ORIGIN, {
    home,
    worktreeOf: async (a) => (a === 'handline.x' ? wt : null),
    open: async (p) => (log.push(`open ${p}`), p.endsWith('refused.md') ? 'no opener' : ''),
    reveal: (p) => void log.push(`reveal ${p}`),
  });
  return { o, log };
}
const call = (o: Opener, path: unknown, agent?: string) => o.openPath(top, agent === undefined ? { path } : { path, agent });

test('a plain file, a plain folder and a path under ~ open in the default app, by their real path', async () => {
  const { o, log } = make();
  assert.deepEqual(await call(o, 'docs/a.md', 'handline.x'), { ok: true, how: 'opened' });
  assert.deepEqual(await call(o, 'plain', 'handline.x'), { ok: true, how: 'opened' });
  assert.deepEqual(await call(o, '~/n.txt'), { ok: true, how: 'opened' });
  assert.deepEqual(await call(o, path.join(wt, 'docs/a.md')), { ok: true, how: 'opened' });
  assert.deepEqual(log, [
    `open ${path.join(wt, 'docs/a.md')}`,
    `open ${path.join(wt, 'plain')}`,
    `open ${path.join(home, 'n.txt')}`,
    `open ${path.join(wt, 'docs/a.md')}`,
  ]);
});

test('a thing that runs is revealed, not opened: a script in any case, an executable, an app, any directory with an extension, a link to an executable', async () => {
  const { o, log } = make();
  for (const p of ['RUN.SH', 'tool', 'x.app', 'thing.bundle', 'link-to-sh'])
    assert.deepEqual(await call(o, p, 'handline.x'), { ok: true, how: 'revealed' }, p);
  assert.equal(
    log.every((l) => l.startsWith('reveal ')),
    true,
  );
  assert.ok(log.includes(`reveal ${fs.realpathSync('/bin/sh')}`)); // the real path of the link, not the link
});

test('a link out of the worktree is judged by what it points to; the page decides nothing else about where a path is', async () => {
  const { o, log } = make();
  assert.deepEqual(await call(o, 'link-out.md', 'handline.x'), { ok: true, how: 'opened' });
  assert.deepEqual(log, [`open ${path.join(root, 'outside.md')}`]);
});

test('a relative path needs an agent with a worktree and stays inside it; a missing file is not_found', async () => {
  const { o, log } = make();
  for (const [p, a] of [
    ['docs/a.md', undefined],
    ['docs/a.md', 'no.worktree'],
    ['../outside.md', 'handline.x'],
    ['docs/nope.md', 'handline.x'],
    ['/nope/nothing.md', undefined],
  ] as const) {
    const r = await call(o, p, a);
    assert.equal(r.ok === false && r.code === 'not_found', true, `${p} ${a}`);
  }
  assert.deepEqual(log, []);
});

test('only a path: links, UNC and drive-less shapes, control characters, NUL, long strings and extra keys are bad_request and nothing is opened', async () => {
  const { o, log } = make();
  for (const p of [
    'file:///etc/passwd',
    'http://x.example/a',
    'smb://h/s',
    '\\\\host\\share',
    '//host/share',
    '\\\\?\\C:\\x',
    'a\u0000b',
    'a\nb',
    '',
    'x'.repeat(4096),
  ]) {
    const r = await call(o, p);
    assert.equal(r.ok === false && r.code === 'bad_request', true, JSON.stringify(p).slice(0, 30));
  }
  assert.equal((await o.openPath(top, { path: 'x', extra: 1 })).ok, false);
  assert.equal((await o.openPath(top, 'x')).ok, false);
  assert.equal((await o.openPath(top, { path: 7 })).ok, false);
  assert.equal((await o.openPath(top, { path: 'x', agent: 7 })).ok, false);
  const r = await call(o, 'C:\\x'); // a drive letter is one letter: not a scheme, and not found on this machine
  assert.equal(r.ok === false && r.code === 'not_found', true);
  assert.deepEqual(log, []);
});

test('a call from another frame or origin is refused', async () => {
  const { o, log } = make();
  for (const e of [
    { senderFrame: { url: `${ORIGIN}/`, parent: {} } },
    { senderFrame: { url: 'https://evil.example/', parent: null } },
    { senderFrame: null },
  ]) {
    const r = await o.openPath(e, { path: path.join(wt, 'docs/a.md') });
    assert.equal(r.ok === false && r.code === 'bad_request', true);
  }
  assert.deepEqual(log, []);
});

test('an opener that refuses is unavailable with its message', async () => {
  const { o } = make();
  fs.writeFileSync(path.join(wt, 'refused.md'), 'x');
  const r = await call(o, 'refused.md', 'handline.x');
  assert.deepEqual(r, { ok: false, code: 'unavailable', message: 'no opener' });
});
