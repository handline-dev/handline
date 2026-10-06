import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Store } from '@handline/app-core';
import { toState } from '../mobile/store.ts';
import http from 'node:http';
import { handler, listenOptions } from '../mobile/server.ts';
import { app, esc, pageKey } from '../mobile/views.ts';
import { setup, until } from '@handline/fake-daemon/helpers';

const A = 'handline.term-build';

async function started() {
  const { client, daemon, net } = setup();
  const store = new Store(client);
  await store.start();
  return { store, daemon, net, client };
}

test('the agent list shows every agent with its state, and follows the daemon', async () => {
  const { store } = await started();
  assert.deepEqual(
    toState(store)
      .agents.map((a) => a.name)
      .sort(),
    ['handline.architect', 'handline.auditor', A],
  );
  let html = app(toState(store));
  assert.match(html, /handline\.architect<\/b><small>idle · Web UI design · acct alpha/);
  assert.match(html, /<span class="flag warn">wall<\/span>/); // the auditor's account is at the wall
  await store.openAgent(A);
  await store.startAgent(A);
  await until(() => toState(store).agents.find((a) => a.name === A)?.state === 'idle', 'state event');
  html = app(toState(store));
  assert.ok(html.includes('class="tabs"'));
  store.stop();
});

test('opening an agent shows Chat by default as an empty state, and Terminal on its tab', async () => {
  const { store } = await started();
  await store.openAgent(A);
  let html = app(toState(store));
  assert.match(html, /aria-selected="true" class="on" data-act="tab" data-tab="chat"/);
  assert.match(html, /Chat is not available yet/);
  assert.doesNotMatch(html, /class="composer"/);
  store.setTab('terminal');
  html = app(toState(store));
  assert.match(html, /class="grid"/);
  assert.match(html, /data-key="esc"/);
  assert.match(html, /data-key="shift-tab"/);
  store.stop();
});

test('start, input and stop go to the daemon; its refusals reach the screen in its words', async () => {
  const { store, daemon } = await started();
  await store.openAgent(A);
  await store.send(A, 'hello');
  assert.match(store.error ?? '', /no open session/);
  assert.match(app(toState(store)), /role="alert"/);

  await store.startAgent(A);
  assert.equal(store.error, null);
  assert.equal(store.details.get(A)!.session_open, true);
  await until(() => store.page!.view.text().some((l) => l.startsWith('Claude Code')), 'banner');
  store.setTab('terminal');
  assert.match(app(toState(store)), /Claude Code v2\.1/);

  await store.send(A, 'push the branch');
  await until(() => store.page!.view.text().includes('> push the branch'), 'echo');
  await store.interrupt(A);
  await store.key(A, 'shift-tab');
  assert.deepEqual(
    daemon.sessions.get(A)!.received.map((r) => `${r.kind}:${r.value}`),
    ['text:push the branch', 'key:esc', 'key:shift-tab'],
  );

  await store.stopAgent(A, 'close');
  assert.equal(store.details.get(A)!.session_open, false);
  assert.match(app(toState(store)), /data-act="resume"/);
  await store.resume(A);
  assert.equal(store.details.get(A)!.session_open, true);
  store.stop();
});

test('a permission prompt shows as a card on both tabs and Yes answers it', async () => {
  const { store, daemon } = await started();
  await store.openAgent(A);
  await store.startAgent(A);
  await store.send(A, 'go');
  daemon.settle();
  await until(() => store.details.get(A)!.prompt !== null, 'prompt card');
  const prompt = store.details.get(A)!.prompt!;
  for (const tab of ['chat', 'terminal'] as const) {
    store.setTab(tab);
    const html = app(toState(store));
    assert.match(html, /git push origin buddyron\/handline\.term-build/);
    assert.match(html, new RegExp(`class="btn yes" data-act="answer" data-key="1" data-prompt="${prompt.id}"`));
    assert.match(html, /class="btn no" data-act="answer" data-key="2"/);
  }
  await store.answer(prompt, '1');
  daemon.settle();
  await until(() => store.details.get(A)!.prompt === null, 'card closed');
  assert.doesNotMatch(app(toState(store)), /class="card prompt"/);
  store.stop();
});

test('a lost connection shows a banner and the page recovers on reconnect', async () => {
  const { store, net, client } = await started();
  await store.openAgent(A);
  net.down = true;
  (client as unknown as { transport: { close(): void } }).transport.close();
  await until(() => store.conn === 'reconnecting', 'reconnecting');
  assert.match(app(toState(store)), /Reconnecting/);
  net.down = false;
  await until(() => store.conn === 'open', 'open');
  assert.doesNotMatch(app(toState(store)), /Reconnecting/);
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
  const before = app(toState(store));
  daemon.daemonTypes(A, 'more output');
  await until(() => store.page!.view.text().includes('> more output'), 'frame');
  const after = app(toState(store));
  const strip = (h: string) => h.replace(/(<pre class="grid"[^>]*>)[\s\S]*?<\/pre>/, '$1</pre>');
  assert.match(after, /more output/);
  assert.equal(strip(after), strip(before));
  assert.equal(pageKey(toState(store)), `agent:${A}:terminal`);
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

test('plain http is refused on any host but loopback', () => {
  assert.equal(listenOptions([]).host, '127.0.0.1');
  assert.equal(listenOptions(['--host', 'localhost']).host, 'localhost');
  assert.throws(() => listenOptions(['--host', '0.0.0.0']), /refusing plain http/);
  assert.throws(() => listenOptions(['--host', '192.168.1.5', '--port', '80']), /refusing plain http/);
  assert.deepEqual(listenOptions(['--cert', 'c.pem', '--key', 'k.pem']), { host: '0.0.0.0', port: 8443, cert: 'c.pem', key: 'k.pem' });
  assert.throws(() => listenOptions(['--cert', 'c.pem']), /go together/);
});
