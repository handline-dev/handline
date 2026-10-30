import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Window } from 'happy-dom';
import { buildId, unsent, UNSENT_PROBE, Updater } from '#src/update.ts';
import { REMIND_MS, UpdateFlow } from '#src/update-flow.ts';

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
const row = (id: number, landing: string) =>
  `<div class="msg person" data-landing="${landing}" data-id="${id}"><div class="msg-body">hi</div></div>`;
const dom = (html: string) => {
  const w = new Window();
  w.document.body.innerHTML = html;
  return w.document as unknown as Parameters<typeof unsent>[0];
};

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
    fs.writeFileSync(files.main, 'x');
    fs.writeFileSync(files.page, '<script src="/assets/a.js">');
    const a = buildId(files);
    assert.equal(buildId(files), a);
    fs.writeFileSync(files.page, '<script src="/assets/b.js">');
    assert.notEqual(buildId(files), a);
  } finally {
    fs.rmSync(dir, { recursive: true });
  }
});

function harness(first: string | null = 'v1') {
  const s = { build: first, idle: true, restarts: 0, fail: '', time: 1_000_000 };
  const flow = new UpdateFlow(() => s.time);
  const u = new Updater(
    {
      build: () => s.build,
      idle: async () => s.idle,
      restart: async () => {
        if (s.fail) throw new Error(s.fail);
        s.restarts++;
      },
    },
    flow,
  );
  return { s, u, flow };
}

test('the same build says nothing; a newer one raises the update as ready, and Restart now restarts', async () => {
  const { s, u, flow } = harness();
  await u.check();
  assert.equal(flow.view.phase, 'idle');
  s.build = 'v2';
  await u.check();
  assert.equal(flow.view.phase, 'ready');
  flow.act('restart');
  await new Promise((r) => setImmediate(r));
  assert.equal(s.restarts, 1);
});

test('while a draft or a send is open it raises nothing, and raises it once the app is idle', async () => {
  const { s, u, flow } = harness();
  s.build = 'v2';
  s.idle = false;
  await u.check();
  await u.check();
  assert.equal(flow.view.phase, 'idle');
  s.idle = true;
  await u.check();
  assert.equal(flow.view.phase, 'ready');
});

test('Later puts it off for a day; it is not raised again by every check, and comes back after the day', async () => {
  const { s, u, flow } = harness();
  s.build = 'v2';
  await u.check();
  flow.act('later');
  assert.equal(flow.view.phase, 'later');
  s.time += REMIND_MS - 1;
  await u.check();
  assert.equal(flow.view.phase, 'later');
  s.time += 2;
  await u.check();
  assert.equal(flow.view.phase, 'ready');
});

test('a restart the daemon refuses leaves the app running and the notice failed with the reason; Try again restarts', async () => {
  const { s, u, flow } = harness();
  s.build = 'v2';
  s.fail = 'the daemon did not upgrade';
  await u.check();
  flow.act('restart');
  await new Promise((r) => setImmediate(r));
  assert.deepEqual([flow.view.phase, flow.view.reason, s.restarts], ['failed', 'the daemon did not upgrade', 0]);
  s.fail = '';
  flow.act('retry');
  await new Promise((r) => setImmediate(r));
  assert.equal(s.restarts, 1);
});

test('a build that cannot be read is skipped, and a check while one is being built or already offered does nothing more', async () => {
  const { s, u, flow } = harness();
  s.build = null;
  await u.check();
  assert.equal(flow.view.phase, 'idle');
  s.build = 'v2';
  flow.preparing('Building (1 of 2)', 0.5); // the follow-main path is building it
  await u.check();
  assert.equal(flow.view.phase, 'preparing');
});
