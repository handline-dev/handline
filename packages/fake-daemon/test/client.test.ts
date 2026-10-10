import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { RpcError } from '@handline/client';
import { type Event } from '@handline/client';
import { refused } from '../src/errors.ts';
import { flush, second, setup, until } from '../src/helpers.ts';

const A = 'handline.term-build';
const W = 'handline.auditor'; // its account is at the wall

test('hello reports protocol, role, caller and the methods the role may call', async () => {
  const { client } = setup();
  const h = await client.start();
  assert.equal(h.protocol, 1);
  assert.equal(h.role, 'founder');
  assert.equal(h.caller.name, 'web-test');
  assert.equal(h.methods.find((m) => m.name === 'session.keys')?.allowed, true);
  client.close();
});

test('an agent role is refused the person-at-the-terminal methods at the role gate', async () => {
  const { client } = setup({ role: 'agent' });
  const h = await client.start();
  assert.equal(h.methods.find((m) => m.name === 'session.input')?.allowed, false);
  assert.equal(h.methods.find((m) => m.name === 'session.start')?.allowed, true);
  await assert.rejects(client.sendInput(A, 'x'), (e: RpcError) => e.code === 'refused' && e.gate === 'role');
  client.close();
});

test('errors carry a stable code, a readable sentence and the gate', async () => {
  const { client } = setup();
  await client.start();
  await assert.rejects(client.call('agent.get', { agent: 'nobody' }), (e: RpcError) => e.code === 'not_found' && /nobody/.test(e.message));
  await assert.rejects(
    client.sendInput(A, 'x'),
    (e: RpcError) => e.code === 'refused' && e.gate === 'state' && /no open session/.test(e.message),
  );
  await assert.rejects(client.sendKeys('handline.architect', 'f13'), (e: RpcError) => e.code === 'bad_request' && e.data?.field === 'keys');
  await assert.rejects(client.call('worktree.diff', { path: '/' }), (e: RpcError) => e.code === 'refused' && e.gate === 'unsupported');
  client.close();
});

test('a daemon that needs a newer client is refused with the fix', async () => {
  const { client, daemon } = setup();
  const inner = daemon as unknown as { dispatch(c: unknown, m: string, p: unknown): object };
  const dispatch = inner.dispatch.bind(daemon);
  inner.dispatch = (c, m, p) => (m === 'hello' ? { ...dispatch(c, m, p), min_client: 2 } : dispatch(c, m, p));
  await assert.rejects(client.start(), (e: RpcError) => e.code === 'protocol' && /hl service install/.test(e.reason ?? ''));
});

test('session started, text streamed, input sent, Esc sent, prompt answered', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.subscribe(['agents', 'notifications']);
  const events: Event[] = [];
  client.onEvent((e) => events.push(e));

  const view = await client.attachTerminal(A);
  assert.deepEqual(view.text(), []);

  await client.startSession(A);
  await until(() => view.text().some((l) => l.startsWith('Claude Code')), 'banner');
  const detail = await client.call('agent.get', { agent: A });
  assert.equal(detail.session_open, true);
  assert.equal(detail.terminal, view.terminal);

  // input, then Esc while the turn runs
  await client.sendInput(A, 'push the branch');
  await until(() => view.text().includes('> push the branch'), 'echo');
  daemon.tick(); // "Thinking…"
  await until(() => view.text().includes('● Thinking…'), 'streamed text');
  await client.sendKeys(A, 'esc');
  await until(() => view.text().some((l) => l.includes('Interrupted')), 'interrupt');
  assert.deepEqual(
    daemon.sessions.get(A)!.received.map((r) => `${r.kind}:${r.value}`),
    ['text:push the branch', 'key:esc'],
  );

  // second turn runs to the permission prompt
  await client.sendInput(A, 'push again');
  daemon.settle();
  await until(() => events.some((e) => e.type === 'prompt_asked'), 'prompt event');
  const [prompt] = await client.openPrompts(A);
  assert.equal(prompt!.tool, 'Bash');
  assert.match(prompt!.command!, /git push/);
  assert.ok(view.text().includes('Do you want to proceed?'));

  // typing while the dialog is open is refused; answering is not
  await assert.rejects(client.sendInput(A, 'yes'), (e: RpcError) => e.code === 'refused' && e.gate === 'screen_blocker');
  await client.answerPrompt(A, prompt!.id, '1');
  daemon.settle();
  await until(() => view.text().includes('● Pushed. Done.'), 'finish');
  assert.equal((await client.call('agent.get', { agent: A })).state, 'idle');
  assert.ok(
    events.some(
      (e) => e.type === 'agent.changed' && (e.data as { has_dialog?: boolean }).has_dialog === false && e.actor?.name === 'web-test',
    ),
  );

  // a fresh attach has the whole text, scrollback included
  const again = await client.attachTerminal(A);
  assert.deepEqual(again.text(), daemon.sessions.get(A)!.lines.slice(0, again.text().length));
  assert.equal(again.text().length, daemon.sessions.get(A)!.lines.length);
  client.close();
});

test('a styled attach carries colour in the snapshot, the frames and the scrollback', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  for (let i = 0; i < 40; i++) daemon.daemonTypes(A, `line ${i}`);
  const view = await client.attachTerminal(A);
  assert.equal(view.format, 'styled');
  const prompt = view.lines().find((r) => r.text === '> line 39');
  assert.deepEqual(prompt?.spans, [{ from: 0, to: 1, style: { fg: 6 } }]);
  daemon.daemonTypes(A, 'later');
  await until(() => view.lines().some((r) => r.text === '> later' && r.spans), 'a coloured frame row');
  await until(() => view.markers.length > 0, 'the marker');
  daemon.daemonTypes(A, 'and again'); // the marker took the attach's counter, so the next frame still fits
  await until(() => view.lines().some((r) => r.text === '> and again' && r.spans), 'the frame after the marker');
  while (view.historyMore) await client.loadOlder(view, 9);
  assert.ok(
    view
      .lines()
      .filter((r) => r.text.startsWith('> line'))
      .every((r) => r.spans?.[0]?.style.fg === 6),
  );
  client.close();
});

test('a host that cannot serve styled is asked again for plain text', async () => {
  const { client, daemon } = setup();
  const inner = daemon as unknown as { dispatch(c: unknown, m: string, p: { format?: string }): object };
  const dispatch = inner.dispatch.bind(daemon);
  inner.dispatch = (c, m, p) => {
    if (m === 'terminal.attach' && p.format === 'styled') throw refused('unsupported', 'this host has no cell attributes');
    return dispatch(c, m, p);
  };
  await client.start();
  await client.startSession(A);
  const view = await client.attachTerminal(A);
  assert.equal(view.format, 'text');
  assert.ok(view.lines().every((r) => !r.spans));
  client.close();
});

test('scrollback pages in by absolute index', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  for (let i = 0; i < 40; i++) daemon.daemonTypes(A, `line ${i}`);
  const view = await client.attachTerminal(A);
  const lines = daemon.sessions.get(A)!.lines;
  assert.equal(view.historyFirst + view.history.length, lines.length - 24);
  while (view.historyMore) await client.loadOlder(view, 7);
  assert.equal(view.historyFirst, 0);
  assert.deepEqual(view.text(), lines.slice(0, view.text().length));
  client.close();
});

test('a stale prompt answer sends nothing and names the open prompt', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  await client.sendInput(A, 'go');
  daemon.settle();
  const [p1] = await client.openPrompts(A);
  await client.sendKeys(A, 'esc'); // the prompt closes
  await client.sendInput(A, 'again');
  daemon.settle();
  const [p2] = await client.openPrompts(A);
  const before = daemon.sessions.get(A)!.received.length;
  await assert.rejects(
    client.answerPrompt(A, p1!.id, '1'),
    (e: RpcError) => e.code === 'stale_prompt' && (e.data!.current as { id: string }).id === p2!.id,
  );
  assert.equal(daemon.sessions.get(A)!.received.length, before);
  client.close();
});

test('at the wall keys pass and typed input is refused', async () => {
  const { client, daemon } = setup();
  Object.assign(daemon.sessions.get(W)!, { structured: false, hasTerminal: true }); // seeded as a structured session; this one types at a terminal
  await client.start();
  await assert.rejects(client.sendInput(W, 'x'), (e: RpcError) => e.code === 'refused' && e.gate === 'wall');
  await client.sendKeys(W, 'esc');
  assert.deepEqual(
    daemon.sessions.get(W)!.received.map((r) => r.value),
    ['esc'],
  );
  assert.equal((await client.call('agents.list', {})).agents.find((a) => a.name === W)!.block?.kind, 'wall');
  client.close();
});

test('the input holder: others are refused, release hands back to the daemon', async () => {
  const { client: a, daemon, net } = setup({ client: 'web-a' });
  await a.start();
  await a.startSession(A);
  const b = second(daemon, net, { client: 'phone-b', reconnect: false });
  await b.start();
  const taken = await a.takeInput(A);
  assert.equal(taken.holder.name, 'web-a');
  assert.deepEqual(
    (await b.call('agent.get', { agent: A })).input_holders!.map((h) => h.name),
    ['web-a'],
  );
  await assert.rejects(b.sendInput(A, 'hi'), (e: RpcError) => e.gate === 'input_holder' && /web-a holds/.test(e.message));
  assert.throws(() => daemon.daemonTypes(A, 'task'), /holds the input/);
  await a.releaseInput(A);
  await b.sendInput(A, 'hi');
  a.close();
  b.close();
});

test('stop, restart and park pass the lifecycle gates, not the holder gate', async () => {
  const { client: a, daemon, net } = setup({ client: 'web-a' });
  await a.start();
  await a.startSession(A);
  await a.takeInput(A);
  const b = second(daemon, net, { client: 'phone-b', reconnect: false });
  await b.start();
  await b.restartSession(A);
  await b.parkSession(A);
  assert.equal((await a.call('agent.get', { agent: A })).state, 'parked');
  await b.startSession(A);
  await b.stopSession(A, 'exit');
  assert.equal((await a.call('agent.get', { agent: A })).session_open, false);
  a.close();
  b.close();
});

test('the daemon typing is marked in the stream', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  const view = await client.attachTerminal(A);
  daemon.daemonTypes(A, 'T1 deliver');
  await until(() => view.markers.length === 1, 'marker');
  assert.equal(view.markers[0]!.kind, 'daemon_typed');
  assert.equal(view.markers[0]!.actor?.kind, 'daemon');
  assert.ok(view.text().includes('> T1 deliver'));
  client.close();
});

test('a retried write with the same request_id is not repeated and says so', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  const first = await client.call('session.input', { agent: A, text: 'once', enter: true, request_id: 'r-1' });
  const retry = await client.call('session.input', { agent: A, text: 'once', enter: true, request_id: 'r-1' });
  assert.equal(first.replayed, undefined);
  assert.equal(retry.replayed, true);
  assert.equal(daemon.sessions.get(A)!.received.length, 1);
  await assert.rejects(
    client.call('session.input', { agent: A, text: 'other', enter: true, request_id: 'r-1' }),
    (e: RpcError) => e.code === 'bad_request' && e.data?.field === 'request_id',
  );
  client.close();
});

test('a remounted terminal keeps the shared view until its last holder lets go', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  const attached = () =>
    [...(daemon as unknown as { conns: Set<{ attached: Set<string> }> }).conns].reduce((n, c) => n + c.attached.size, 0);
  const first = client.attachTerminal(A),
    second = client.attachTerminal(A); // remount before the first answer
  const [v1, v2] = await Promise.all([first, second]);
  assert.equal(v1, v2);
  await client.detachTerminal(A); // the cancelled mount lets go
  assert.equal(attached(), 1);
  await client.sendInput(A, 'still live');
  await until(() => v2.text().some((l) => l.includes('still live')), 'frames reach the remaining holder');
  await client.detachTerminal(A);
  assert.equal(attached(), 0);
  client.close();
});

test('a restart appends to the screen an attached client already shows', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.startSession(A);
  const view = await client.attachTerminal(A);
  await client.sendInput(A, 'before');
  await client.restartSession(A, true);
  await until(() => view.text().filter((l) => l.startsWith('Claude Code')).length === 2, 'second banner');
  assert.deepEqual(view.text(), daemon.sessions.get(A)!.lines.slice(0, -1)); // the view drops the trailing blank row
  client.close();
});

test('reconnect resumes the event stream without loss and takes a fresh terminal frame', async () => {
  const { client, daemon, net } = setup();
  await client.start();
  await client.subscribe(['agents', 'notifications']);
  await client.startSession(A);
  const view = await client.attachTerminal(A);
  const seen: number[] = [];
  client.onEvent((e) => seen.push(e.cursor));
  const states: string[] = [];
  client.onState((s) => states.push(s));

  await client.sendInput(A, 'one');
  daemon.settle();
  await until(() => view.text().includes('Do you want to proceed?'), 'prompt');
  const first = seen.length;
  assert.ok(first > 0);

  net.down = true;
  daemon.dropConnections();
  await until(() => client.state === 'reconnecting', 'drop');
  // changes while nobody is connected: the person answers elsewhere
  const other = second(daemon, { down: false }, { client: 'other', reconnect: false });
  await other.start();
  await other.answerPrompt(A, daemon.sessions.get(A)!.prompt!.id, '2');
  daemon.settle();
  net.down = false;

  await until(() => client.state === 'open' && states.includes('reconnecting'), 'reconnect');
  await until(() => view.text().includes('● Understood, I will not push.'), 'terminal resync');
  await flush();
  assert.ok(seen.length > first, 'events from the outage arrived');
  const all = (daemon as unknown as { events: Event[] }).events.map((e: Event) => e.cursor) as number[];
  assert.deepEqual(
    seen,
    all.filter((c) => c >= seen[0]!),
    'every event from the first seen on, once, in order',
  );
  assert.deepEqual(view.text(), daemon.sessions.get(A)!.lines.slice(-view.text().length));
  client.close();
  other.close();
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
  const gaps: { reason: string; cursor: number }[] = [];
  const got: number[] = [];
  client.onGap((g) => gaps.push(g));
  client.onEvent((e) => got.push(e.cursor));
  net.down = true;
  daemon.dropConnections();
  await until(() => client.state === 'reconnecting', 'drop');
  for (let i = 0; i < 6; i++) daemon.emit('agents', 'agent.changed', { agent: A });
  net.down = false;
  await until(() => client.state === 'open', 'reconnect');
  await until(() => gaps.length === 1, 'gap');
  assert.equal(gaps[0]!.reason, 'retention');
  assert.deepEqual(got, [], 'nothing replayed past the gap');
  daemon.emit('agents', 'agent.changed', { agent: A });
  await until(() => got.length === 1 && got[0] === gaps[0]!.cursor + 1, 'stream continues after the gap');
  client.close();
});

test('close settles a call waiting for the connection to come back', async () => {
  const { client, net } = setup();
  await client.start();
  net.down = true;
  (client as unknown as { transport: { close(): void } }).transport.close();
  await until(() => client.state === 'reconnecting', 'drop');
  const waiting = client.call('daemon.status', {});
  client.close();
  await assert.rejects(waiting, (e: RpcError) => e.code === 'transport');
});
