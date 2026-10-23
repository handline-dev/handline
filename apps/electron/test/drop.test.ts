import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { Sender } from '#src/bridge.ts';
import { Dropper } from '#src/drop.ts';

const ORIGIN = 'http://127.0.0.1:5000';
const top: Sender = { senderFrame: { url: `${ORIGIN}/`, parent: null } };
const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'hl-drop-')));
after(() => fs.rmSync(root, { recursive: true, force: true }));
const wt = path.join(root, 'wt');
const desk = path.join(root, 'Desktop');
fs.mkdirSync(wt);
fs.mkdirSync(path.join(desk, 'proj'), { recursive: true });
fs.writeFileSync(path.join(desk, 'a.md'), 'hello');
fs.writeFileSync(path.join(desk, 'proj', 'b.txt'), 'inner');
const d = new Dropper(ORIGIN, async (a) => (a === 'handline.x' ? wt : null));

test('a file and a folder are copied into the worktree, each drop to its own folder, git ignoring them', async () => {
  const one = await d.bringIn(top, { path: path.join(desk, 'a.md'), agent: 'handline.x' });
  const two = await d.bringIn(top, { path: path.join(desk, 'a.md'), agent: 'handline.x' });
  const dir = await d.bringIn(top, { path: path.join(desk, 'proj'), agent: 'handline.x' });
  assert.ok(one.ok && two.ok && dir.ok);
  assert.ok(one.path.startsWith(path.join(wt, '.handline-drops') + path.sep));
  assert.notEqual(one.path, two.path);
  assert.equal(fs.readFileSync(one.path, 'utf8'), 'hello');
  assert.equal(fs.readFileSync(path.join(dir.path, 'b.txt'), 'utf8'), 'inner');
  assert.equal(fs.readFileSync(path.join(wt, '.handline-drops', '.gitignore'), 'utf8'), '*\n');
});

test('refused: another frame, an extra key, a relative path, an agent without a worktree, a missing file', async () => {
  const other: Sender = { senderFrame: { url: 'http://evil.test/', parent: null } };
  const p = path.join(desk, 'a.md');
  assert.equal((await d.bringIn(other, { path: p, agent: 'handline.x' })).ok, false);
  assert.equal((await d.bringIn(top, { path: p, agent: 'handline.x', more: 1 })).ok, false);
  assert.equal((await d.bringIn(top, { path: 'a.md', agent: 'handline.x' })).ok, false);
  const none = await d.bringIn(top, { path: p, agent: 'nobody' });
  assert.ok(!none.ok && none.code === 'unavailable');
  const gone = await d.bringIn(top, { path: path.join(desk, 'no.md'), agent: 'handline.x' });
  assert.ok(!gone.ok && gone.code === 'not_found');
});
