import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Store } from '../ui/store.ts';
import { controlsOf } from '../ui/model.ts';
import { setup, until } from './helpers.ts';

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
