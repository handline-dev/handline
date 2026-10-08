import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { NavigateTo } from '@handline/app-core';
import { makeApi, type Ipc } from '../src/api.ts';
import { CHANNELS, Connection, fromWindow, Page, type Sender } from '../src/bridge.ts';
import { noticeFor } from '../src/notices.ts';
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
  let push: ((e: unknown, to: NavigateTo) => void) | null = null;
  const ipc: Ipc & { fail?: Error } = {
    async invoke(channel, arg) {
      calls.push([channel, arg]);
      if (ipc.fail) throw ipc.fail;
      return { ok: true };
    },
    on: (_c, f) => void (push = f),
  };
  return { ipc, calls, click: (to: NavigateTo) => push!(null, to) };
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

test('the page switches the mode and pairs another device only from the window and with the right arguments', async () => {
  const seen: unknown[] = [];
  const c = new Connection(ORIGIN, {
    mode: () => ({ mode: 'connect', remote: 'mac.ts.net:7443' }),
    setMode: (m) => void seen.push(m),
    pair: async (i) => {
      if (i.code === 'bad') throw new Error('the gateway at x cannot be paired with: nothing answered');
      seen.push(i);
    },
  });
  const evil: Sender = { senderFrame: { url: 'https://evil.example/', parent: null } };
  assert.deepEqual(c.mode(top), { ok: true, mode: 'connect', remote: 'mac.ts.net:7443' });
  assert.equal(c.mode(evil).ok, false);
  assert.deepEqual(c.setMode(top, 'own'), { ok: true });
  assert.equal(c.setMode(top, 'nonsense').ok, false);
  assert.equal(c.setMode(evil, 'own').ok, false);
  assert.deepEqual(await c.pairRemote(top, { address: 'a.ts.net', code: 'c' }), { ok: true });
  assert.equal((await c.pairRemote(top, { address: 'a.ts.net', code: 'c', extra: 'x' })).ok, false);
  assert.equal((await c.pairRemote(top, { code: 7 })).ok, false);
  assert.equal((await c.pairRemote(evil, { link: 'handline://x' })).ok, false);
  const failed = await c.pairRemote(top, { address: 'a.ts.net', code: 'bad' });
  assert.deepEqual(failed, { ok: false, code: 'cannot_pair', message: 'the gateway at x cannot be paired with: nothing answered' });
  assert.deepEqual(seen, ['own', { address: 'a.ts.net', code: 'c' }]);
});
