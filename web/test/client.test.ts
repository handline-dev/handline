import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RpcError } from '../src/client.ts';
import type { EventNotice } from '../src/protocol.ts';
import { flush, setup, until } from './helpers.ts';

const A = 'handline.term-build';

test('hello reports protocol, role and the methods the role may call', async () => {
  const { client } = setup();
  const h = await client.start();
  assert.equal(h.protocol, 1);
  assert.equal(h.role, 'founder');
  assert.ok('session.keys' in h.methods);
  client.close();
});

test('an agent role does not get the person-at-the-terminal methods and is refused them', async () => {
  const { client } = setup({ role: 'agent' });
  const h = await client.start();
  assert.ok(!('session.input' in h.methods));
  await assert.rejects(client.sendInput(A, 'x'), (e: RpcError) => e.code === 'refused');
  client.close();
});

test('errors carry a stable code and a readable sentence', async () => {
  const { client } = setup();
  await client.start();
  await assert.rejects(client.call('agent.get', { agent: 'nobody' }), (e: RpcError) => e.code === 'not_found' && /nobody/.test(e.message));
  await assert.rejects(client.call('session.input', { agent: A, text: 'x', enter: true }), (e: RpcError) => e.code === 'refused' && /no open session/.test(e.message));
  client.close();
});

test('a daemon that needs a newer client is refused with the fix', async () => {
  const { client, daemon } = setup();
  const dispatch = (daemon as any).dispatch.bind(daemon);
  (daemon as any).dispatch = (c: unknown, m: string, p: unknown) =>
    (m === 'hello' ? { ...dispatch(c, m, p), min_client: 2 } : dispatch(c, m, p));
  await assert.rejects(client.start(), (e: RpcError) => e.code === 'protocol' && /hl service install/.test(e.reason ?? ''));
});

test('session started, text streamed, input sent, Esc sent, prompt answered', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.subscribe(['agents', 'requests']);
  const events: EventNotice[] = [];
  client.onEvent((e) => events.push(e));

  const view = await client.attachTerminal(A);
  assert.deepEqual(view.text(), []);

  await client.startSession(A);
  await until(() => view.text().some((l) => l.startsWith('Claude Code')), 'banner');
  assert.equal((await client.call('agent.get', { agent: A })).session_open, true);

  // input, then Esc while the turn runs
  await client.sendInput(A, 'push the branch');
  await until(() => view.text().includes('> push the branch'), 'echo');
  daemon.tick(); // "Thinking…"
  await until(() => view.text().includes('● Thinking…'), 'streamed text');
  await client.sendKeys(A, 'esc');
  await until(() => view.text().some((l) => l.includes('Interrupted')), 'interrupt');
  assert.deepEqual(daemon.sessions.get(A)!.received.map((r) => `${r.kind}:${r.value}`), ['text:push the branch', 'key:esc']);

  // second turn runs to the permission prompt
  await client.sendInput(A, 'push again');
  daemon.settle();
  await until(() => events.some((e) => e.kind === 'prompt_open'), 'prompt event');
  const [prompt] = await client.openPrompts(A);
  assert.equal(prompt.tool, 'Bash');
  assert.match(prompt.command!, /git push/);
  assert.ok(view.text().includes('Do you want to proceed?'));

  // typing while the dialog is open is refused; answering is not
  await assert.rejects(client.sendInput(A, 'yes'), (e: RpcError) => e.code === 'refused' && /dialog/.test(e.message));
  await client.answerPrompt(prompt.id, '1');
  daemon.settle();
  await until(() => view.text().includes('● Pushed. Done.'), 'finish');
  assert.equal((await client.call('agent.get', { agent: A })).state, 'idle');
  assert.ok(events.some((e) => e.kind === 'prompt_closed' && e.data?.how === 'yes' && e.actor === 'web-test'));

  // the view equals the daemon's screen, scrollback included
  assert.deepEqual(view.text(), daemon.sessions.get(A)!.lines);
  client.close();
});

test('a stale prompt answer sends nothing', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  await client.sendInput(A, 'go'); daemon.settle();
  const [p1] = await client.openPrompts(A);
  await client.sendKeys(A, 'esc'); // the prompt closes
  await client.sendInput(A, 'again'); daemon.settle();
  const before = daemon.sessions.get(A)!.received.length;
  await assert.rejects(client.answerPrompt(p1.id, '1'), (e: RpcError) => e.code === 'stale_prompt');
  assert.equal(daemon.sessions.get(A)!.received.length, before);
  client.close();
});

test('the input holder: others are refused, release hands back to the daemon', async () => {
  const { client: a, daemon } = setup({ client: 'web-a' });
  await a.start();
  await a.startSession(A);
  const b = new (a.constructor as any)({ client: 'phone-b', reconnect: false, connect: async () => { const { memoryPair } = await import('../src/transport.ts'); const [x, y] = memoryPair(); daemon.accept(y); return x; } });
  await b.start();
  await a.takeInput(A);
  await assert.rejects(b.sendInput(A, 'hi'), (e: RpcError) => e.code === 'refused' && /web-a holds/.test(e.message));
  assert.throws(() => daemon.daemonTypes(A, 'task'), /holds the input/);
  await a.releaseInput(A);
  await b.sendInput(A, 'hi');
  a.close(); b.close();
});

test('the daemon typing is marked in the stream', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  const view = await client.attachTerminal(A);
  daemon.daemonTypes(A, 'T1 deliver');
  await until(() => view.markers.length === 1, 'marker');
  assert.equal(view.markers[0].kind, 'daemon_typed');
  assert.ok(view.text().includes('> T1 deliver'));
  client.close();
});

test('a retried write with the same request_id is not repeated', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  await client.call('session.input', { agent: A, text: 'once', enter: true, request_id: 'r-1' });
  await client.call('session.input', { agent: A, text: 'once', enter: true, request_id: 'r-1' });
  assert.equal(daemon.sessions.get(A)!.received.length, 1);
  await assert.rejects(client.call('session.input', { agent: A, text: 'x', enter: true, request_id: undefined as any, ...{ request_id: 5 as any } }), (e: RpcError) => e.code === 'bad_request');
  client.close();
});

test('reconnect resumes the event stream without loss and takes a fresh terminal frame', async () => {
  const { client, daemon, net } = setup();
  await client.start();
  await client.subscribe(['agents', 'requests']);
  await client.startSession(A);
  const view = await client.attachTerminal(A);
  const seen: number[] = [];
  client.onEvent((e) => seen.push(e.cursor));
  const states: string[] = [];
  client.onState((s) => states.push(s));

  await client.sendInput(A, 'one'); daemon.settle();
  await until(() => view.text().includes('Do you want to proceed?'), 'prompt');
  const first = seen.length;
  assert.ok(first > 0);

  net.down = true;
  daemon.dropConnections();
  await until(() => client.state === 'reconnecting', 'drop');
  // changes while nobody is connected: the person answers elsewhere
  daemon.sessions.get(A)!.queue = [];
  (daemon as any).dispatch({ client: 'other', role: 'founder', helloed: true }, 'prompt.answer',
    { prompt: daemon.sessions.get(A)!.prompt!.id, choice: '2', request_id: 'r-other' });
  daemon.settle();
  net.down = false;

  await until(() => client.state === 'open' && states.includes('reconnecting'), 'reconnect');
  await until(() => view.text().includes('● Understood, I will not push.'), 'terminal resync');
  await flush();
  assert.deepEqual(seen, [...new Set(seen)].sort((x, y) => x - y), 'cursors strictly increase, none twice');
  assert.ok(seen.length > first, 'events from the outage arrived');
  assert.ok(seen.slice(first).some((c) => c > 0));
  const all = (daemon as any).events.map((e: any) => e.cursor);
  assert.deepEqual(seen, all.filter((c: number) => c >= seen[0]), 'every event from the first seen on, in order');
  assert.deepEqual(view.text(), daemon.sessions.get(A)!.lines);
  client.close();
});

test('a write in flight when the connection drops is resent and done once', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  const done = client.sendInput(A, 'survive');
  daemon.dropConnections();
  await done;
  assert.equal(daemon.sessions.get(A)!.received.filter((r) => r.value === 'survive').length, 1);
  client.close();
});

test('a cursor older than retention gets a gap, then the stream continues', async () => {
  const { client, daemon, net } = setup({ daemon: { retention: 3 } });
  await client.start();
  await client.subscribe(['agents']);
  const gaps: number[] = [];
  const got: number[] = [];
  client.onGap((g) => gaps.push(g.cursor));
  client.onEvent((e) => got.push(e.cursor));
  net.down = true;
  daemon.dropConnections();
  await until(() => client.state === 'reconnecting', 'drop');
  for (let i = 0; i < 6; i++) daemon.emit('agents', 'state', A);
  net.down = false;
  await until(() => client.state === 'open', 'reconnect');
  await until(() => gaps.length === 1, 'gap');
  assert.deepEqual(got, [], 'nothing replayed past the gap');
  daemon.emit('agents', 'state', A);
  await until(() => got.length === 1 && got[0] === gaps[0] + 1, 'stream continues after the gap');
  client.close();
});
