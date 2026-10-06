import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Store } from '../mobile/store.ts';
import http from 'node:http';
import { handler } from '../mobile/server.ts';
import { app, esc, pageKey } from '../mobile/views.ts';
import { setup, until } from './helpers.ts';

const A = 'handline.term-build';

async function started() {
  const { client, daemon, net } = setup();
  const store = new Store(client);
  await store.start();
  return { store, daemon, net, client };
}

test('the agent list shows every agent with its state, and follows the daemon', async () => {
  const { store } = await started();
  assert.deepEqual(store.state.agents.map((a) => a.name).sort(), ['handline.architect', 'handline.auditor', A]);
  let html = app(store.state);
  assert.match(html, /handline\.architect<\/b><small>idle · Web UI design · acct alpha/);
  assert.match(html, /<span class="flag warn">wall<\/span>/); // the auditor's account is at the wall
  await store.openAgent(A);
  await store.startAgent(A);
  await until(() => store.state.agents.find((a) => a.name === A)?.state === 'idle', 'state event');
  html = app(store.state);
  assert.ok(html.includes('class="tabs"'));
  store.stop();
});

test('opening an agent shows Chat by default as an empty state, and Terminal on its tab', async () => {
  const { store } = await started();
  await store.openAgent(A);
  let html = app(store.state);
  assert.match(html, /aria-selected="true" class="on" data-act="tab" data-tab="chat"/);
  assert.match(html, /Chat is not available yet/);
  assert.doesNotMatch(html, /class="composer"/);
  store.setTab('terminal');
  html = app(store.state);
  assert.match(html, /class="grid"/);
  assert.match(html, /data-key="esc"/);
  assert.match(html, /data-key="shift-tab"/);
  store.stop();
});

test('start, input and stop go to the daemon; its refusals reach the screen in its words', async () => {
  const { store, daemon } = await started();
  await store.openAgent(A);
  await store.send(A, 'hello');
  assert.match(store.state.error ?? '', /no open session/);
  assert.match(app(store.state), /role="alert"/);

  await store.startAgent(A);
  assert.equal(store.state.error, null);
  assert.equal(store.state.page!.detail.session_open, true);
  await until(() => store.state.page!.view.text().some((l) => l.startsWith('Claude Code')), 'banner');
  store.setTab('terminal');
  assert.match(app(store.state), /Claude Code v2\.1/);

  await store.send(A, 'push the branch');
  await until(() => store.state.page!.view.text().includes('> push the branch'), 'echo');
  await store.interrupt(A);
  await store.key(A, 'shift-tab');
  assert.deepEqual(daemon.sessions.get(A)!.received.map((r) => `${r.kind}:${r.value}`), ['text:push the branch', 'key:esc', 'key:shift-tab']);

  await store.stopAgent(A, 'close');
  assert.equal(store.state.page!.detail.session_open, false);
  assert.match(app(store.state), /data-act="resume"/);
  await store.resume(A);
  assert.equal(store.state.page!.detail.session_open, true);
  store.stop();
});

test('a permission prompt shows as a card on both tabs and Yes answers it', async () => {
  const { store, daemon } = await started();
  await store.openAgent(A);
  await store.startAgent(A);
  await store.send(A, 'go'); daemon.settle();
  await until(() => store.state.page!.detail.prompt !== null, 'prompt card');
  const prompt = store.state.page!.detail.prompt!;
  for (const tab of ['chat', 'terminal'] as const) {
    store.setTab(tab);
    const html = app(store.state);
    assert.match(html, /git push origin buddyron\/handline\.term-build/);
    assert.match(html, new RegExp(`class="btn yes" data-act="answer" data-key="1" data-prompt="${prompt.id}"`));
    assert.match(html, /class="btn no" data-act="answer" data-key="2"/);
  }
  await store.answer(prompt, '1'); daemon.settle();
  await until(() => store.state.page!.detail.prompt === null, 'card closed');
  assert.doesNotMatch(app(store.state), /class="card prompt"/);
  store.stop();
});

test('answering a prompt that changed is refused and the page shows the open one', async () => {
  const { store, daemon } = await started();
  await store.openAgent(A);
  await store.startAgent(A);
  await store.send(A, 'go'); daemon.settle();
  await until(() => store.state.page!.detail.prompt !== null, 'first prompt');
  const first = store.state.page!.detail.prompt!;
  await store.interrupt(A);
  await until(() => store.state.page!.detail.prompt === null, 'closed');
  await store.send(A, 'again'); daemon.settle();
  await until(() => store.state.page!.detail.prompt !== null && store.state.page!.detail.prompt.id !== first.id, 'second prompt');
  await store.answer(first, '1');
  assert.ok(store.state.error);
  assert.notEqual(store.state.page!.detail.prompt!.id, first.id);
  store.stop();
});

test('a lost connection shows a banner and the page recovers on reconnect', async () => {
  const { store, net, client } = await started();
  await store.openAgent(A);
  net.down = true;
  (client as any).transport.close();
  await until(() => store.state.connection === 'reconnecting', 'reconnecting');
  assert.match(app(store.state), /Reconnecting/);
  net.down = false;
  await until(() => store.state.connection === 'open', 'open');
  assert.doesNotMatch(app(store.state), /Reconnecting/);
  store.stop();
});

test('text from the daemon is escaped', () => {
  assert.equal(esc('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');
});

test('streaming terminal output changes only the grid region, never the page key or the composer', async () => {
  const { store, daemon } = await started();
  await store.openAgent(A);
  await store.startAgent(A);
  store.setTab('terminal');
  const before = app(store.state);
  daemon.daemonTypes(A, 'more output');
  await until(() => store.state.page!.view.text().includes('> more output'), 'frame');
  const after = app(store.state);
  const strip = (h: string) => h.replace(/(<pre class="grid"[^>]*>)[\s\S]*?<\/pre>/, '$1</pre>');
  assert.match(after, /more output/);
  assert.equal(strip(after), strip(before));
  assert.equal(pageKey(store.state), `agent:${A}:terminal`);
  store.stop();
});

test('the static server answers a malformed or directory URL with an error, not a crash', async () => {
  const server = http.createServer(handler);
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const status = async (u: string) => (await fetch(base + u)).status;
  assert.equal(await status('/web/mobile/%'), 400);
  assert.equal(await status('/web/mobile/..'), 404);
  assert.equal(await status('/web/mobile/'), 404);
  assert.equal(await status('/web/mobile/server.ts'), 404);
  assert.equal(await status('/web/mobile/app.ts'), 200);
  assert.equal(await status('/'), 200);
  assert.equal(await status('/web/mobile/icon-192.png'), 200);
  server.close();
});
