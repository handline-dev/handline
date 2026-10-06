import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { buildId, unsent, UNSENT_PROBE, Updater } from '../src/update.ts';

const page = (over: { fields?: string[]; matches?: Record<string, number> } = {}) => ({
  querySelectorAll: (s: string) => {
    if (s.includes('textarea')) return (over.fields ?? []).map((value) => ({ value }));
    return { length: over.matches?.[s.split(',')[0]!] ?? 0 } as ArrayLike<{ value?: string }>;
  },
});

test('a restart waits for an open box, text in a field or a send that has not landed', () => {
  assert.equal(unsent(page()), false);
  assert.equal(unsent(page({ fields: ['', '  '] })), false);
  assert.equal(unsent(page({ fields: ['half a message'] })), true);
  assert.equal(unsent(page({ matches: { 'dialog[open]': 1 } })), true);
  assert.equal(unsent(page({ matches: { '[data-landing="queued"]': 1 } })), true);
});

test('the probe runs in the page as the same function', () => {
  const run = new Function('document', `return ${UNSENT_PROBE}`);
  assert.equal(run(page({ fields: ['x'] })), true);
  assert.equal(run(page()), false);
});

test('a build is the page that names its assets plus the main bundle, and null while one is missing', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hl-build-'));
  const files = { page: path.join(dir, 'index.html'), main: path.join(dir, 'main.cjs') };
  try {
    assert.equal(buildId(files), null);
    fs.writeFileSync(files.main, 'x'); fs.writeFileSync(files.page, '<script src="/assets/a.js">');
    const a = buildId(files);
    assert.equal(buildId(files), a);
    fs.writeFileSync(files.page, '<script src="/assets/b.js">');
    assert.notEqual(buildId(files), a);
  } finally { fs.rmSync(dir, { recursive: true }); }
});

function harness(first: string | null = 'v1') {
  const s = { build: first, idle: true, answer: true, asked: 0, restarts: 0 };
  const u = new Updater({
    build: () => s.build, idle: async () => s.idle, ask: async () => { s.asked++; return s.answer; }, restart: () => { s.restarts++; },
  });
  return { s, u };
}

test('the same build asks nothing; a newer one asks once and restarts on yes', async () => {
  const { s, u } = harness();
  await u.check();
  assert.equal(s.asked, 0);
  s.build = 'v2';
  await u.check();
  assert.deepEqual([s.asked, s.restarts], [1, 1]);
});

test('while a draft or a send is open it does not ask, and asks once it is idle', async () => {
  const { s, u } = harness();
  s.build = 'v2'; s.idle = false;
  await u.check(); await u.check();
  assert.equal(s.asked, 0);
  s.idle = true;
  await u.check();
  assert.equal(s.asked, 1);
});

test('a build said no to is not asked about again, a newer one is', async () => {
  const { s, u } = harness();
  s.build = 'v2'; s.answer = false;
  await u.check(); await u.check(); await u.check();
  assert.deepEqual([s.asked, s.restarts], [1, 0]);
  s.build = 'v3';
  await u.check();
  assert.equal(s.asked, 2);
});

test('a build that cannot be read is skipped, and a check during a question does not ask twice', async () => {
  const { s, u } = harness();
  s.build = null;
  await u.check();
  assert.equal(s.asked, 0);
  s.build = 'v2';
  await Promise.all([u.check(), u.check()]);
  assert.equal(s.asked, 1);
});

test('when no build was readable at launch, the first one seen is the running one', async () => {
  const { s, u } = harness(null);
  s.build = 'v1';
  await u.check();
  assert.equal(s.asked, 0);
});
