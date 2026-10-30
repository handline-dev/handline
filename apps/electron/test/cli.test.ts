import { afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { cliState, linkCli } from '#src/cli.ts';

const made: string[] = [];
const scratch = () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hl-cli-'));
  made.push(dir);
  return dir;
};
afterEach(() => {
  for (const dir of made.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

test('linking puts hl on the path, moves an old link, and never replaces a real file', () => {
  const dir = path.join(scratch(), 'bin');
  const bundled = path.join(scratch(), 'hl');
  fs.writeFileSync(bundled, '#!/bin/sh\n');
  assert.deepEqual(cliState(bundled, dir), { bundled: true, linked: false, link: path.join(dir, 'hl') });
  linkCli(bundled, dir);
  assert.equal(cliState(bundled, dir).linked, true);
  const moved = path.join(scratch(), 'hl');
  fs.writeFileSync(moved, '');
  linkCli(moved, dir);
  assert.equal(cliState(bundled, dir).linked, false);
  assert.equal(cliState(moved, dir).linked, true);
  fs.unlinkSync(path.join(dir, 'hl'));
  fs.writeFileSync(path.join(dir, 'hl'), 'mine');
  assert.throws(() => linkCli(bundled, dir), /already a file/);
  assert.equal(fs.readFileSync(path.join(dir, 'hl'), 'utf8'), 'mine');
});

test('an app run from a checkout has no hl to link', () => {
  assert.deepEqual(cliState(null, '/x'), { bundled: false, linked: false, link: '/x/hl' });
});
