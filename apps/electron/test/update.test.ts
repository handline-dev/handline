import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Window } from 'happy-dom';
import { buildId, unsent, UNSENT_PROBE, Updater } from '../src/update.ts';

const page = (over: { fields?: string[]; matches?: Record<string, number> } = {}) => ({
  querySelectorAll: (s: string) => {
    if (s.includes('textarea')) return (over.fields ?? []).map((value) => ({ value }));
    return { length: over.matches?.[s.split(',')[0]!] ?? 0 } as ArrayLike<{ value?: string }>;
  },
});

test('a restart waits for an open box, text in a field or a send in flight', () => {
  assert.equal(unsent(page()), false);
  assert.equal(unsent(page({ fields: ['', '  '] })), false);
  assert.equal(unsent(page({ fields: ['half a message'] })), true);
  assert.equal(unsent(page({ matches: { 'dialog[open]': 1 } })), true);
  assert.equal(unsent(page({ matches: { '[data-id^="-"]': 1 } })), true);
});

// U8's message row, as ChatTab renders it: data-id is negative until the daemon numbers the message.
const row = (id: number, landing: string) => `<div class="msg person" data-landing="${landing}" data-id="${id}"><div class="msg-body">hi</div></div>`;
const dom = (html: string) => { const w = new Window(); w.document.body.innerHTML = html; return w.document as unknown as Parameters<typeof unsent>[0]; };

test("against U8's markup: only a row the daemon has not numbered, a marked element or typed text counts; a queued message does not", () => {
  assert.equal(unsent(dom(row(41, 'queued') + row(42, 'typed') + '<textarea class="compose"></textarea>')), false);
  assert.equal(unsent(dom(row(41, 'queued') + row(-1790000000000, 'queued'))), true);
  assert.equal(unsent(dom('<div data-pending="send"></div>')), true);
  assert.equal(unsent(dom('<textarea class="compose">half</textarea>')), true);
  assert.equal(unsent(dom('<dialog open></dialog>')), true);
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
