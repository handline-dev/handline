import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { NavigateTo } from '@handline/app-core';
import { makeApi, type Ipc } from '#src/api.ts';
import { Cli, CHANNELS, Connection, fromWindow, Page, Settings, type Sender } from '#src/bridge.ts';
import { noticeFor } from '#src/notices.ts';
import type { Event } from '@handline/client';

const ORIGIN = 'http://127.0.0.1:5000';
const top: Sender = { senderFrame: { url: `${ORIGIN}/#/agents/x`, parent: null } };

test("only the window's top frame on the served origin may call", () => {
  assert.equal(fromWindow(top, ORIGIN), true);
  assert.equal(fromWindow({ senderFrame: { url: `${ORIGIN}/`, parent: {} } }, ORIGIN), false); // a child frame
  assert.equal(fromWindow({ senderFrame: { url: 'http://127.0.0.1:6000/', parent: null } }, ORIGIN), false); // another port
  assert.equal(fromWindow({ senderFrame: { url: 'https://evil.example/', parent: null } }, ORIGIN), false);
  assert.equal(fromWindow({ senderFrame: { url: 'not a url', parent: null } }, ORIGIN), false);
  assert.equal(fromWindow({ senderFrame: null }, ORIGIN), false);
});

test('screen keeps the last agent, null clears it, and a call from another sender or with a bad argument changes nothing', () => {
  const page = new Page(ORIGIN);
  assert.equal(page.agent, null); // before the first call
  assert.deepEqual(page.screen(top, { agent: 'handline.architect' }), { ok: true });
  assert.equal(page.agent, 'handline.architect');
  const refused = [
    page.screen({ senderFrame: { url: 'https://evil.example/', parent: null } }, { agent: 'x' }),
    page.screen(top, { agent: 7 }),
    page.screen(top, { agent: 'x', extra: 1 }),
    page.screen(top, {}),
    page.screen(top, 'x'),
    page.screen(top, null),
    page.screen(top, { agent: 'x'.repeat(513) }),
  ];
  for (const r of refused) assert.equal(r.ok === false && r.code === 'bad_request', true);
  assert.equal(page.agent, 'handline.architect');
  assert.deepEqual(page.screen(top, { agent: null }), { ok: true });
  assert.equal(page.agent, null);
});

test('unsent is unknown until the page calls it, then follows the page; only a boolean from the window counts', () => {
  const page = new Page(ORIGIN);
  assert.equal(page.unsent, null);
  assert.deepEqual(page.setUnsent(top, true), { ok: true });
  assert.equal(page.unsent, true);
  assert.deepEqual(page.setUnsent(top, false), { ok: true });
  assert.equal(page.unsent, false);
  for (const arg of ['true', 1, null, { on: true }, undefined]) assert.equal(page.setUnsent(top, arg).ok, false);
  assert.equal(page.setUnsent({ senderFrame: null }, true).ok, false);
  assert.equal(page.unsent, false);
});

function fakeIpc() {
  const calls: [string, unknown][] = [];
  const push = new Map<string, (e: unknown, arg: unknown) => void>();
  const ipc: Ipc & { fail?: Error } = {
    async invoke(channel, arg) {
      calls.push([channel, arg]);
      if (ipc.fail) throw ipc.fail;
      return { ok: true };
    },
    on: (c, f) => void push.set(c, f),
  };
  return {
    ipc,
    calls,
    click: (to: NavigateTo) => push.get(CHANNELS.navigate)!(null, to),
    fullScreen: (on: boolean) => push.get(CHANNELS.fullScreen)!(null, on),
  };
}

test('the page API says api 1 and sends each call on its own channel', async () => {
  const { ipc, calls } = fakeIpc();
  const api = makeApi(ipc);
  assert.equal(api.api, 1);
  assert.deepEqual(await api.screen({ agent: 'a' }), { ok: true });
  assert.deepEqual(await api.unsent(true), { ok: true });
  assert.deepEqual(calls, [
    [CHANNELS.screen, { agent: 'a' }],
    [CHANNELS.unsent, true],
  ]);
});

test('onFullScreen tells the state at once and each change, until it is stopped', () => {
  const f = fakeIpc();
  const api = makeApi(f.ipc);
  const seen: boolean[] = [];
  const off = api.onFullScreen!((on) => seen.push(on));
  f.fullScreen(true);
  f.fullScreen(false);
  off();
  f.fullScreen(true);
  assert.deepEqual(seen, [false, true, false]);
});

test('a call that the shell fails comes back as a result, not a throw', async () => {
  const f = fakeIpc();
  f.ipc.fail = new Error('no handler');
  const r = await makeApi(f.ipc).screen({ agent: 'a' });
  assert.deepEqual(r, { ok: false, code: 'bad_request', message: 'no handler' });
});

test('a click reaches the listener; a click before any listener is kept and delivered once to the first', () => {
  const { ipc, click } = fakeIpc();
  const api = makeApi(ipc);
  const to = { screen: 'agent', id: 'handline.architect', event: 'stall' };
  click(to); // before the page registered
  const got: NavigateTo[] = [];
  const off = api.onNavigate((t) => got.push(t));
  assert.deepEqual(got, [to]);
  const second: NavigateTo[] = [];
  api.onNavigate((t) => second.push(t));
  assert.deepEqual(second, []); // delivered once
  click({ screen: 'task', id: '4', event: 'task_done' });
  assert.equal(got.length, 1); // the newest listener has it
  assert.equal(second.length, 1);
  off(); // already replaced: does not drop the newer listener
  click({ screen: 'task', id: '5', event: 'task_done' });
  assert.equal(second.length, 2);
});

test('a notice carries its event type and target, so a click can go where the daemon said', () => {
  const e = {
    cursor: 1,
    topic: 'notifications',
    type: 'task_done',
    at: 't',
    actor: null,
    agent: 'handline.pc-build',
    title: null,
    target: { screen: 'task', id: '7' },
    data: { task: 7 },
  } as Event;
  const n = noticeFor(e)!;
  assert.deepEqual([n.target, n.event], [{ screen: 'task', id: '7' }, 'task_done']);
  assert.equal(noticeFor({ ...e, target: null })!.target, null);
});

test('a finished task shows its title and result, else Task N done over the agent, and the number only once', () => {
  const e = {
    cursor: 2,
    topic: 'notifications',
    type: 'task_done',
    at: 't',
    actor: null,
    agent: 'handline.pc.auditor',
    title: 'T5720 done',
    target: { screen: 'task', id: '5720' },
    data: { task: 5720, task_title: 'Audit PR #536', result: 'PASS' },
  } as Event;
  const full = noticeFor(e)!;
  assert.deepEqual([full.title, full.body], ['Audit PR #536', 'handline.pc.auditor: PASS']);
  const bare = noticeFor({ ...e, data: { task: 5720 } } as Event)!;
  assert.deepEqual([bare.title, bare.body], ['Task 5720 done', 'handline.pc.auditor']);
  for (const n of [full, bare]) assert.equal(`${n.title} ${n.body}`.split('5720').length - 1, n === full ? 0 : 1);
});

test('the page switches the mode and pairs another device only from the window and with the right arguments', async () => {
  const seen: unknown[] = [];
  const c = new Connection(ORIGIN, {
    mode: () => ({ mode: 'connect', remote: 'mac.ts.net:7443' }),
    build: () => ({ sha: 'abc123def456', canRestart: false }),
    restartDaemon: async () => {
      throw new Error('on another device');
    },
    phone: async () => {
      throw new Error('paired with another device');
    },
    follow: () => ({ sha: 'abc1234', state: 'available', next: 'def5678' }),
    check: async () => void seen.push('check'),
    setMode: (m) => void seen.push(m),
    pair: async (i) => {
      if (i.code === 'bad') throw new Error('the gateway at x cannot be paired with: nothing answered');
      seen.push(i);
    },
  });
  const evil: Sender = { senderFrame: { url: 'https://evil.example/', parent: null } };
  assert.deepEqual(c.mode(top), { ok: true, mode: 'connect', remote: 'mac.ts.net:7443', phone: false });
  assert.equal(c.mode(evil).ok, false);
  assert.deepEqual(c.follow(top), { ok: true, following: true, sha: 'abc1234', state: 'available', next: 'def5678' });
  assert.equal(c.follow(evil).ok, false);
  assert.deepEqual(await c.checkUpdate(top), { ok: true });
  assert.equal((await c.checkUpdate(evil)).ok, false);
  assert.deepEqual(c.setMode(top, 'own'), { ok: true });
  assert.equal(c.setMode(top, 'nonsense').ok, false);
  assert.equal(c.setMode(evil, 'own').ok, false);
  assert.deepEqual(await c.pairRemote(top, { address: 'a.ts.net', code: 'c' }), { ok: true });
  assert.equal((await c.pairRemote(top, { address: 'a.ts.net', code: 'c', extra: 'x' })).ok, false);
  assert.equal((await c.pairRemote(top, { code: 7 })).ok, false);
  assert.equal((await c.pairRemote(evil, { link: 'handline://x' })).ok, false);
  const failed = await c.pairRemote(top, { address: 'a.ts.net', code: 'bad' });
  assert.deepEqual(failed, { ok: false, code: 'cannot_pair', message: 'the gateway at x cannot be paired with: nothing answered' });
  assert.deepEqual(await c.phone(top), { ok: false, code: 'cannot_pair', message: 'paired with another device' });
  assert.equal((await c.phone(evil)).ok, false);
  assert.deepEqual(seen, ['check', 'own', { address: 'a.ts.net', code: 'c' }]);
});

test('settings: only get and set with checked arguments reach the daemon, and only from the window', async () => {
  const calls: unknown[][] = [];
  const s = new Settings(ORIGIN, {
    get: async (agent) => (calls.push(['get', agent]), { values: {} } as never),
    set: async (changes, agent) => (calls.push(['set', changes, agent]), { accepted: true } as never),
  });
  const evil: Sender = { senderFrame: { url: 'https://evil.example/', parent: null } };
  assert.equal((await s.get(evil, {})).ok, false);
  assert.equal((await s.set(evil, { changes: { active_cap: 3 } })).ok, false);
  const badSet = [
    undefined,
    null,
    {},
    { changes: {} },
    { changes: [] },
    { changes: { 'Bad Key': 1 } },
    { changes: { dev_mode: true } }, // a daemon setting the page does not edit
    { changes: { active_cap: 3, input_hold_idle_s: 5 } },
    { changes: { active_cap: {} } },
    { changes: { active_cap: Infinity } },
    { changes: { active_cap: 3 }, method: 'agent.remove' },
    { changes: { active_cap: 3 }, agent: '' },
    { changes: { active_cap: 3 }, agent: 7 },
  ];
  for (const a of badSet) assert.deepEqual({ ...(await s.set(top, a)), message: '' }, { ok: false, code: 'bad_request', message: '' });
  for (const a of [[], 'x', { agent: 7 }, { extra: 1 }])
    assert.deepEqual({ ...(await s.get(top, a)), message: '' }, { ok: false, code: 'bad_request', message: '' });
  assert.deepEqual(calls, []);
  assert.equal((await s.get(top, undefined)).ok, true);
  assert.equal((await s.get(top, { agent: 'a.b' })).ok, true);
  assert.equal((await s.set(top, { changes: { active_cap: 3, handoff_note: null }, agent: 'a.b' })).ok, true);
  assert.deepEqual(calls, [
    ['get', undefined],
    ['get', 'a.b'],
    ['set', { active_cap: 3, handoff_note: null }, 'a.b'],
  ]);
});

test('settings: a daemon refusal comes back as a result, never a throw', async () => {
  const refuse = Object.assign(new Error('not allowed'), { code: 'refused' });
  const s = new Settings(ORIGIN, { get: () => Promise.reject(refuse), set: () => Promise.reject(new Error('down')) });
  assert.deepEqual(await s.get(top, {}), { ok: false, code: 'refused', message: 'not allowed' });
  assert.deepEqual(await s.set(top, { changes: { active_cap: 1 } }), { ok: false, code: 'unavailable', message: 'down' });
});

test('the page sees and links the carried hl only from the window, and a refusal comes back as a sentence', () => {
  let linked = 0;
  const cli = new Cli(ORIGIN, {
    state: () => ({ bundled: true, linked: false, link: '/h/.local/bin/hl' }),
    link: () => {
      if (linked++) throw new Error('/h/.local/bin/hl is already a file: remove it first');
    },
  });
  const evil: Sender = { senderFrame: { url: 'https://evil.example/', parent: null } };
  assert.deepEqual(cli.state(top), { ok: true, bundled: true, linked: false, link: '/h/.local/bin/hl' });
  assert.equal(cli.state(evil).ok, false);
  assert.deepEqual(cli.link(top), { ok: true });
  assert.deepEqual(cli.link(top), { ok: false, code: 'cannot_link', message: '/h/.local/bin/hl is already a file: remove it first' });
  assert.equal(cli.link(evil).ok, false);
  assert.equal(linked, 2);
});

test('settings: the status bar resource area keys reach the daemon', async () => {
  const sent: unknown[] = [];
  const s = new Settings(ORIGIN, { get: async () => ({}) as never, set: async (c) => (sent.push(c), { accepted: true } as never) });
  const changes = { keep_awake: 'on', load_per_core_pct: 200, ram_free_min_pct: 5, heavy_slots: 3 };
  for (const [k, v] of Object.entries(changes)) assert.equal((await s.set(top, { changes: { [k]: v } })).ok, true, k);
  assert.deepEqual(
    sent,
    Object.entries(changes).map(([k, v]) => ({ [k]: v })),
  );
});

test('the page asks for the app build and restarts the daemon only from the window; a refusal carries its reason', async () => {
  const c = new Connection(ORIGIN, {
    mode: () => ({ mode: 'own', remote: null }),
    build: () => ({ sha: 'abc123def456', canRestart: true }),
    restartDaemon: async () => {
      throw new Error('the daemon did not upgrade: refused');
    },
    phone: async () => undefined,
    follow: () => null,
    check: async () => undefined,
    setMode: () => undefined,
    pair: async () => undefined,
  });
  const win = { senderFrame: { url: `${ORIGIN}/`, parent: null } };
  const frame = { senderFrame: { url: `${ORIGIN}/`, parent: {} } };
  assert.deepEqual(c.build(win), { ok: true, sha: 'abc123def456', canRestart: true });
  assert.equal(c.build(frame).ok, false);
  assert.deepEqual(await c.restartDaemon(win), { ok: false, code: 'cannot_restart', message: 'the daemon did not upgrade: refused' });
  assert.equal((await c.restartDaemon(frame)).ok, false);
});
