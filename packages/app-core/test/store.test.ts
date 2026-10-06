import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Store, controlsOf } from '../src/index.ts';
import { setup, until } from '@handline/fake-daemon/helpers';

const ARCH = 'handline.architect'; // idle with a session
const TB = 'handline.term-build'; // parked, no session

async function open() {
  const s = setup();
  const store = new Store(s.client);
  await store.start();
  return { ...s, store };
}

test('the store lists the agents and keeps a watched agent current from events', async () => {
  const { store, client, daemon } = await open();
  assert.equal(store.agents.size, 3);
  assert.equal(store.me, 'web-test');
  assert.equal(store.chat, false);
  await store.watch(ARCH);
  assert.equal(store.details.get(ARCH)!.session_open, true);
  await store.act('type', () => client.takeInput(ARCH));
  await until(() => controlsOf(store.details.get(ARCH)!, store.me).typing, 'my hold shows');
  await store.act('type', () => client.sendInput(ARCH, 'push it'));
  daemon.settle();
  await until(() => store.details.get(ARCH)!.prompt !== null, 'permission prompt');
  assert.equal(store.agents.get(ARCH)!.state, 'waiting');
  assert.ok(store.notes.get(ARCH)!.some((n) => /took the input/.test(n.text)));
  client.close();
});

test('answering the open prompt works, and a stale prompt is refused with a message and refreshed', async () => {
  const { store, client, daemon } = await open();
  await store.watch(ARCH);
  await store.act('take', () => client.takeInput(ARCH));
  await store.act('type', () => client.sendInput(ARCH, 'go'));
  daemon.settle();
  await until(() => store.details.get(ARCH)!.prompt !== null);
  const id = store.details.get(ARCH)!.prompt!.id;
  assert.equal(await store.act('answer', () => client.answerPrompt(ARCH, 'old-id', '1')), undefined);
  assert.match(store.error!, /no longer the open one/);
  assert.ok(await store.act('answer', () => client.answerPrompt(ARCH, id, '1')));
  assert.equal(store.error, null);
  await until(() => store.details.get(ARCH)!.prompt === null, 'prompt cleared');
  client.close();
});

test('start then stop moves the controls; a refusal reads as the gate\'s sentence', async () => {
  const { store, client } = await open();
  await store.watch(TB);
  assert.equal(controlsOf(store.details.get(TB)!, store.me).start, true);
  await store.act('start', () => client.startSession(TB));
  await until(() => store.details.get(TB)!.session_open, 'session opens');
  assert.equal(controlsOf(store.details.get(TB)!, store.me).stop, true);
  assert.equal(await store.act('type', () => client.sendInput('handline.auditor', 'x')), undefined); // its account is at the wall
  assert.match(store.error!, /^type: the account is at its usage limit/);
  await store.act('close', () => client.stopSession(TB, 'close'));
  await until(() => !store.details.get(TB)!.session_open, 'session closes');
  client.close();
});

test('after a reconnect the store reloads the agent list', async () => {
  const { store, client, daemon, net } = await open();
  net.down = true; daemon.dropConnections();
  await until(() => store.conn === 'reconnecting');
  daemon.sessions.get(ARCH)!.state = 'running'; // changed while no event could reach the page
  net.down = false;
  await until(() => store.conn === 'open');
  await until(() => store.agents.get(ARCH)!.state === 'running', 'state after resume');
  client.close();
});

// ---- the open agent page (folded in from the phone store's tests) ----

test('opening an agent page starts on Chat, Terminal is a tab away, and closing it detaches', async () => {
  const { store, client } = await open();
  await store.openAgent(TB);
  assert.equal(store.page!.agent, TB);
  assert.equal(store.page!.tab, 'chat');
  assert.ok(store.details.get(TB), 'the page watches its agent');
  store.setTab('terminal');
  assert.equal(store.page!.tab, 'terminal');
  await store.openAgent(ARCH); // opening another closes the first
  assert.equal(store.page!.agent, ARCH);
  await store.closeAgent();
  assert.equal(store.page, null);
  store.setTab('chat'); // no page: nothing happens
  assert.equal(store.page, null);
  client.close();
});

test('start, input, stop and resume reach the daemon; its refusals arrive in its words and clear on the next success', async () => {
  const { store, daemon, client } = await open();
  await store.openAgent(TB);
  await store.send(TB, 'hello');
  assert.match(store.error!, /no open session/);
  await store.send(TB, '   '); // blank text is not sent
  await store.startAgent(TB);
  assert.equal(store.error, null);
  assert.equal(store.details.get(TB)!.session_open, true); // the detail is read again after the action
  await until(() => store.page!.view.text().some((l) => l.startsWith('Claude Code')), 'banner');
  await store.send(TB, 'push the branch');
  await until(() => store.page!.view.text().includes('> push the branch'), 'echo');
  await store.interrupt(TB);
  await store.key(TB, 'shift-tab');
  assert.deepEqual(daemon.sessions.get(TB)!.received.map((r) => `${r.kind}:${r.value}`), ['text:push the branch', 'key:esc', 'key:shift-tab']);
  await store.stopAgent(TB, 'close');
  assert.equal(store.details.get(TB)!.session_open, false);
  await store.resume(TB);
  assert.equal(store.details.get(TB)!.session_open, true);
  store.dismissError();
  client.close();
});

test('a permission prompt is answered from the page; a prompt that changed is refused and the open one is shown', async () => {
  const { store, daemon, client } = await open();
  await store.openAgent(TB);
  await store.startAgent(TB);
  await store.send(TB, 'go'); daemon.settle();
  await until(() => store.details.get(TB)!.prompt !== null, 'first prompt');
  const first = store.details.get(TB)!.prompt!;
  await store.interrupt(TB);
  await until(() => store.details.get(TB)!.prompt === null, 'closed');
  await store.send(TB, 'again'); daemon.settle();
  await until(() => store.details.get(TB)!.prompt !== null && store.details.get(TB)!.prompt!.id !== first.id, 'second prompt');
  await store.answer(first, '1');
  assert.ok(store.error);
  assert.notEqual(store.details.get(TB)!.prompt!.id, first.id);
  await store.answer(store.details.get(TB)!.prompt!, '1'); daemon.settle();
  await until(() => store.details.get(TB)!.prompt === null, 'card closed');
  assert.equal(store.error, null);
  client.close();
});

test('a lost connection shows as the state and the open page recovers on reconnect', async () => {
  const { store, client, daemon, net } = await open();
  await store.openAgent(ARCH);
  net.down = true; daemon.dropConnections();
  await until(() => store.conn === 'reconnecting');
  net.down = false;
  await until(() => store.conn === 'open');
  assert.equal(store.page!.agent, ARCH);
  assert.ok(store.details.get(ARCH));
  store.stop();
});
