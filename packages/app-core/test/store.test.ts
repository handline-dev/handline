// The store keeps the connection, the caller, the error line, the agents' notes and the open agent page. What the query cache owns now
// (the agent list and detail, their events, reconnect and gap refreshes, the actions on an agent and their refusals) is tested in
// `@handline/app-data` and the apps. Where each store test that went has gone:
//   list and watched detail kept current by events      -> app-data test/hooks.test.tsx ("is kept current by events"), test/bridge.test.ts (agents)
//   reload of the list after a reconnect                -> app-data test/hooks.test.tsx ("is read again after a reconnect and after a gap")
//   start, stop, input, interrupt, take, release        -> app-data test/writes.test.tsx (every write), apps/web test/q2.test.tsx, test/u12.test.tsx
//   permission prompt answered, a stale prompt refused  -> apps/web test/q2.test.tsx, apps/mobile test/screens.test.tsx
//   watch counted, a refresh whose detail load fails    -> gone with the watch: react-query counts the observers
//   an agent that is gone while its page is open        -> below (the removed event), apps/mobile Loadable "is gone", apps/web Workspace
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RpcError } from '@handline/client';
import { Store } from '../src/index.ts';
import { setup, until } from '@handline/fake-daemon/helpers';

const ARCH = 'handline.architect'; // idle with a session
const TB = 'handline.term-build'; // parked, no session

async function open() {
  const s = setup();
  const store = new Store(s.client);
  await store.start();
  return { ...s, store };
}

test('the store knows who the caller is and which methods the daemon lets it call', async () => {
  const { store, client } = await open();
  assert.equal(store.conn, 'open');
  assert.ok(store.me);
  assert.equal(store.can('agents.list'), true);
  assert.equal(store.can('no.such.method'), false);
  client.close();
});

test("a failure the person asked about reads as the gate's sentence, and a lost connection says nothing", async () => {
  const { store, client } = await open();
  store.reportError('start', new RpcError('refused', 'its account is at the wall', { hint: 'try later' } as never));
  assert.match(store.error!, /^start: /);
  store.dismissError();
  assert.equal(store.error, null);
  store.reportError('start', new RpcError('transport', 'closed'));
  assert.equal(store.error, null);
  store.fail('daemon unreachable');
  assert.equal(store.error, 'daemon unreachable');
  client.close();
});

test('opening an agent page starts on Chat, Terminal is a tab away, and closing it detaches', async () => {
  const { store, client } = await open();
  await store.openAgent(TB);
  assert.equal(store.page!.agent, TB);
  assert.equal(store.page!.tab, 'chat');
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

test('a structured agent opens with no terminal view, and closing it detaches nothing', async () => {
  const { store, client } = await open();
  await store.openAgent('handline.auditor');
  assert.equal(store.page!.agent, 'handline.auditor');
  assert.equal(store.page!.view, null);
  assert.equal(store.error, null);
  await store.closeAgent();
  assert.equal(store.page, null);
  client.close();
});

test('a lost connection shows as the state and the open page stays open through the reconnect', async () => {
  const { store, daemon, net } = await open();
  await store.openAgent(ARCH);
  net.down = true;
  daemon.dropConnections();
  await until(() => store.conn === 'reconnecting');
  net.down = false;
  await until(() => store.conn === 'open');
  assert.equal(store.page!.agent, ARCH);
  store.stop();
});

test('two quick openAgent calls end with the later page; the earlier agent is not left attached', async () => {
  const { store, client } = await open();
  const detached: string[] = [];
  const detach = client.detachTerminal.bind(client);
  client.detachTerminal = (a: string) => {
    detached.push(a);
    return detach(a);
  };
  // the earlier call is slow to attach, so it finishes after the later one has opened its page
  const attach = client.attachTerminal.bind(client);
  client.attachTerminal = async (a: string) => {
    if (a === ARCH) await new Promise((r) => setTimeout(r, 40));
    return attach(a);
  };
  const first = store.openAgent(ARCH);
  await new Promise((r) => setTimeout(r, 5));
  const second = store.openAgent(TB);
  await Promise.all([first, second]);
  assert.equal(store.page!.agent, TB);
  assert.equal(detached.filter((a) => a === ARCH).length, 1); // attached late, then detached by the stale call
  assert.equal(store.error, null); // being overtaken is not a failure
  await Promise.all([store.openAgent(TB), store.openAgent(TB)]);
  assert.equal(store.page!.agent, TB);
  store.stop();
});

test('quick repeats of the same agent leave no terminal attached after close', async () => {
  const { store, client, daemon } = await open();
  const attach = client.attachTerminal.bind(client);
  client.attachTerminal = async (a: string) => {
    if (a === ARCH) await new Promise((r) => setTimeout(r, 40));
    return attach(a);
  };
  const attached = () =>
    [...(daemon as unknown as { conns: Set<{ attached: Set<string> }> }).conns].reduce((n, c) => n + c.attached.size, 0);
  const first = store.openAgent(ARCH);
  await new Promise((r) => setTimeout(r, 5));
  await Promise.all([first, store.openAgent(ARCH)]);
  assert.equal(attached(), 1);
  await store.closeAgent();
  assert.equal(attached(), 0);
  store.stop();
});

test('a failed open leaves no page and says why', async () => {
  const { store, client } = await open();
  client.attachTerminal = async () => {
    throw new RpcError('refused', 'the agent has no terminal');
  };
  await store.openAgent(TB);
  assert.equal(store.page, null);
  assert.match(store.error!, /no terminal/);
  store.stop();
});

test('stop clears the open page so a restart begins clean', async () => {
  const { store } = await open();
  await store.openAgent(ARCH);
  store.stop();
  assert.equal(store.page, null);
});

test('an agent that is removed while its page is open closes the page and says so', async () => {
  const { store, daemon, client } = await open();
  await store.openAgent(ARCH);
  daemon.emit('agents', 'agent.removed', { agent: ARCH });
  await until(() => store.page === null, 'page closed');
  assert.match(store.error!, /was removed/);
  client.close();
});

test('what happened to an agent while the page watched is kept as notes', async () => {
  const { store, daemon, client } = await open();
  daemon.emit('agents', 'agent.control', { agent: ARCH, data: { at: 't', kind: 'start', text: 'started', actor: { name: 'me' } } });
  await until(() => (store.notes.get(ARCH) ?? []).length === 1, 'a note');
  assert.deepEqual(store.notes.get(ARCH), [{ at: 't', kind: 'start', text: 'started', actor: 'me' }]);
  client.close();
});
