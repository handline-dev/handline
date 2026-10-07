import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Sender } from '../src/bridge.ts';
import { Composer, parseLoginEnv, splitWords, sweepTemp, type EditorEnv } from '../src/editor.ts';

const ORIGIN = 'http://127.0.0.1:5000';
const top: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: null } };
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'hl-ed-'));
const bin = path.join(tmp, 'bin');
fs.mkdirSync(bin);
const script = (name: string, body: string) => {
  const p = path.join(bin, name);
  fs.writeFileSync(p, `#!/bin/sh\n${body}\n`, { mode: 0o755 });
  return p;
};
const appends = script('appends', 'echo "added" >> "$1"; sleep 0.05');
const quick = script('quick', 'exit 0');
const fails = script('fails', 'exit 1');
const slow = script('slow', 'sleep 30');
const symlinks = script('symlinks', 'rm "$1"; ln -s /etc/hosts "$1"; sleep 0.05');
const big = script('big', 'head -c 300000 /dev/zero | tr "\\0" a >> "$1"; sleep 0.05');
const spaced = path.join(bin, 'my editor');
fs.writeFileSync(spaced, '#!/bin/sh\necho spaced >> "$1"; sleep 0.05\n', { mode: 0o755 });
const mk = (env: EditorEnv) => new Composer(ORIGIN, { env: async () => env, tmp });
const left = () => fs.readdirSync(tmp).filter((n) => n.startsWith('handline-'));
const code = (r: { ok: boolean }) => (r.ok ? 'ok' : (r as unknown as { code: string }).code);

test('the editor is split into words without a shell: quotes group, operators are refused', () => {
  assert.deepEqual(splitWords('code --wait'), ['code', '--wait']);
  assert.deepEqual(splitWords(`"/Applications/My Editor/bin/e" -w 'a b'`), ['/Applications/My Editor/bin/e', '-w', 'a b']);
  assert.deepEqual(splitWords('a\\ b c'), ['a b', 'c']);
  for (const bad of ['a; b', 'a | b', 'a && b', 'a > b', '$(x)', 'a `b`', 'a\nb', '(a)', '"open']) assert.equal(splitWords(bad), null, bad);
});

test('an editor that adds a line returns the new text, changed, and the temp directory is gone', async () => {
  const c = mk({ VISUAL: appends, PATH: '' });
  const r = await c.compose(top, { text: 'hello\n' });
  assert.deepEqual(r, { ok: true, text: 'hello\nadded\n', changed: true });
  assert.deepEqual(left(), []);
});

test('VISUAL wins over EDITOR; a quoted path with a space runs; arguments come before the file', async () => {
  assert.equal(((await mk({ VISUAL: `"${spaced}"`, EDITOR: fails, PATH: '' }).compose(top, {})) as { text: string }).text, 'spaced\n');
  assert.equal(code(await mk({ EDITOR: fails, PATH: '' }).compose(top, {})), 'editor_failed');
});

test('the temp file is mode 0600 in a 0700 handline-* directory, named for the chosen extension, and holds only the text', async () => {
  const out = path.join(tmp, 'probe.out');
  const probe = script('probe', `ls -ld "$(dirname "$1")" "$1" > ${out}; cat "$1" >> ${out}; echo x >> "$1"; sleep 0.05`);
  const r = await mk({ VISUAL: probe, PATH: '' }).compose(top, { text: 'secret', ext: 'txt' });
  assert.equal(code(r), 'ok');
  const [dir, file, ...body] = fs.readFileSync(out, 'utf8').split('\n');
  assert.match(dir!, /^drwx------[@+]? .*\/handline-[^/]+$/);
  assert.match(file!, /^-rw-------[@+]? .*\/compose\.txt$/);
  assert.equal(body.join('\n').trim(), 'secret');
});

test('an editor that returns at once with the file unchanged is not_waiting; one that exits 1 is editor_failed', async () => {
  assert.equal(code(await mk({ VISUAL: quick, PATH: '' }).compose(top, { text: 'x' })), 'not_waiting');
  assert.equal(code(await mk({ VISUAL: fails, PATH: '' }).compose(top, { text: 'x' })), 'editor_failed');
  assert.deepEqual(left(), []);
});

test('a terminal editor, no editor, an operator and a missing program are told apart, and none starts anything', async () => {
  assert.equal(code(await mk({ VISUAL: 'vim', PATH: '/nowhere' }).compose(top, {})), 'terminal_editor');
  assert.equal(code(await mk({ EDITOR: '/usr/bin/nano -w' }).compose(top, {})), 'terminal_editor');
  assert.equal(code(await mk({ PATH: '' }).compose(top, {})), 'no_editor');
  assert.equal(code(await mk({ VISUAL: 'code --wait; rm -rf x', PATH: bin }).compose(top, {})), 'bad_editor');
  assert.equal(code(await mk({ VISUAL: 'nope-editor', PATH: bin }).compose(top, {})), 'bad_editor');
  assert.equal(code(await mk({ VISUAL: 'sub/dir', PATH: bin }).compose(top, {})), 'bad_editor');
  assert.deepEqual(left(), []);
});

test('the first word is found on the PATH the shell was given, and not on another', async () => {
  assert.equal(code(await mk({ VISUAL: 'appends', PATH: `/usr/bin:${bin}` }).compose(top, { text: 'a' })), 'ok');
  assert.equal(code(await mk({ VISUAL: 'appends', PATH: '/usr/bin:/bin' }).compose(top, { text: 'a' })), 'bad_editor');
});

test('a second compose while one runs is busy; cancelCompose kills the editor and the compose settles cancelled; the directory is removed', async () => {
  const c = mk({ VISUAL: slow, PATH: '' });
  const first = c.compose(top, { text: 'a' });
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(c.running, true);
  assert.equal(code(await c.compose(top, {})), 'busy');
  assert.deepEqual(await c.cancel(top, undefined), { ok: true });
  assert.equal(code(await first), 'cancelled');
  assert.equal(c.running, false);
  assert.deepEqual(left(), []);
});

test('a compose.md replaced by a symlink is refused, and a file over 256 KiB is too_large', async () => {
  assert.notEqual(code(await mk({ VISUAL: symlinks, PATH: '' }).compose(top, { text: 'a' })), 'ok');
  assert.equal(code(await mk({ VISUAL: big, PATH: '' }).compose(top, { text: 'a' })), 'too_large');
  assert.equal(code(await mk({ VISUAL: appends, PATH: '' }).compose(top, { text: 'a'.repeat(256 * 1024 + 1) })), 'too_large');
  assert.deepEqual(left(), []);
});

test('bad arguments and other senders are bad_request', async () => {
  const c = mk({ VISUAL: appends, PATH: '' });
  for (const a of [{ ext: 'sh' }, { text: 1 }, { extra: 1 }, 'x', [1]]) assert.equal(code(await c.compose(top, a)), 'bad_request');
  assert.equal(code(await c.compose({ senderFrame: { url: 'https://evil.example/', parent: null } }, {})), 'bad_request');
  assert.equal(code(await c.compose({ senderFrame: null }, {})), 'bad_request');
  assert.equal((await c.cancel({ senderFrame: null }, undefined)).ok, false);
});

test('the login shell reply is read between the markers; rc output around it is ignored', () => {
  const out = 'welcome\n__HL_BEGIN__\nVISUAL=code --wait\nEDITOR=\nPATH=/opt/homebrew/bin:/usr/bin\n__HL_END__\nbye\n';
  assert.deepEqual(parseLoginEnv(out), { VISUAL: 'code --wait', EDITOR: undefined, PATH: '/opt/homebrew/bin:/usr/bin' });
  assert.deepEqual(parseLoginEnv('nothing'), { VISUAL: undefined, EDITOR: undefined, PATH: undefined });
});

test('at launch a handline-* directory older than a day is removed and a fresh one stays', async () => {
  const old = path.join(tmp, 'handline-old');
  const fresh = path.join(tmp, 'handline-fresh');
  fs.mkdirSync(old);
  fs.mkdirSync(fresh);
  const t = Date.now() / 1000 - 3 * 24 * 3600;
  fs.utimesSync(old, t, t);
  await sweepTemp(tmp);
  assert.equal(fs.existsSync(old), false);
  assert.equal(fs.existsSync(fresh), true);
  fs.rmSync(fresh, { recursive: true });
});
