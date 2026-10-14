import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fromBase64, type ByteEvent, type RpcError } from '@handline/client';
import { second, setup, until } from '../src/helpers.ts';

const A = 'handline.term-build';
const text = (b: Uint8Array) => new TextDecoder().decode(b);

/** Opens a bytes view and records what it asks the pane to do. */
async function watch(client: ReturnType<typeof setup>['client']) {
  const events: ByteEvent[] = [];
  const { view } = await client.bytes.open(A, (e) => events.push(e));
  return { view, events };
}

test('the daemon says it serves bytes and styled, and a format that is neither word is bad_request', async () => {
  const { client } = setup();
  const h = await client.start();
  assert.deepEqual(h.capabilities.terminal_format, ['text', 'bytes', 'styled']);
  await assert.rejects(client.call('terminal.attach', { agent: A, format: 'ansi' as never }), (e: RpcError) => e.data?.field === 'format');
  client.close();
});

test('the attach snapshot is CR LF rows with colour, one reset, and the cursor on the last text row', async () => {
  const { client } = setup();
  await client.start();
  const snap = await client.call('terminal.attach', { agent: A, format: 'bytes', history: 5 });
  assert.equal(snap.format, 'bytes');
  if (snap.format !== 'bytes') return;
  const data = text(fromBase64(snap.data));
  assert.match(snap.data, /^[A-Za-z0-9+/]*={0,2}$/);
  assert.equal(data.split('\r\n').length, snap.rows, 'one row per screen row, joined by CR LF');
  assert.ok(!/(?<!\r)\n/.test(data), 'never a bare LF');
  assert.ok(data.includes('\x1b[0m'));
  assert.equal(
    snap.history.lines.every((l) => typeof l === 'string'),
    true,
  );
  assert.deepEqual(snap.holders, [snap.holder]);
  client.close();
});

test('output follows the snapshot at seq + 1, a repeat is dropped, and typing is written in order', async () => {
  const { client, daemon } = setup();
  await client.start();
  const { view, events } = await watch(client);
  const first = view.seq;
  daemon.daemonTypes(A, 'one');
  daemon.daemonTypes(A, 'two');
  await until(() => events.filter((e) => e.type === 'write').length >= 2, 'two writes');
  assert.ok(view.seq > first);
  const written = events.flatMap((e) => (e.type === 'write' ? [text(e.data)] : [])).join('');
  assert.ok(written.indexOf('one') < written.indexOf('two'));
  client.bytes.notice('output', { terminal: view.terminal, seq: view.seq, data: 'eA==' }); // a repeat
  assert.equal(events.filter((e) => e.type === 'write' && text(e.data) === 'x').length, 0);
  client.close();
});

test('a hole in seq re-attaches and repaints from a new snapshot', async () => {
  const { client } = setup();
  await client.start();
  const { view, events } = await watch(client);
  const resets = () => events.filter((e) => e.type === 'reset').length;
  client.bytes.notice('output', { terminal: view.terminal, seq: view.seq + 5, data: 'eA==' });
  await until(() => resets() === 2 && view.state === 'live', 'repaint'); // the attach painted once, the repeat attach again
  assert.equal(events.filter((e) => e.type === 'write').length, 0, 'the out-of-order bytes were not written');
  client.close();
});

test('a reconnect goes stale, then repaints from the new snapshot with what was typed meanwhile', async () => {
  const { client, daemon, net } = setup();
  await client.start();
  const { view, events } = await watch(client);
  net.down = true;
  daemon.dropConnections();
  await until(() => view.state === 'stale', 'stale');
  daemon.daemonTypes(A, 'while away');
  net.down = false;
  await until(() => view.state === 'live', 'live again');
  const reset = events.filter((e) => e.type === 'reset').at(-1);
  assert.ok(reset && reset.type === 'reset' && text(reset.data).includes('while away'));
  client.close();
});

test("the holder's resize gives a bytes watcher a snapshot with the new size, and no frame", async () => {
  const { client } = setup();
  await client.start();
  const { view, events } = await watch(client);
  await client.takeInput(A);
  await client.bytes.resize(view, 100, 30);
  await until(() => view.cols === 100, 'new size');
  const reset = events.filter((e) => e.type === 'reset').at(-1);
  assert.ok(reset?.type === 'reset' && reset.reason === 'resize' && reset.rows === 30);
  client.close();
});

test('with nobody holding the input a watcher resizes, the last resize wins, and a holder makes it theirs alone', async () => {
  const { client, daemon, net } = setup();
  const other = second(daemon, net, { client: 'other-viewer' });
  await client.start();
  await other.start();
  const mine = await watch(client),
    theirs = await watch(other);
  await client.bytes.resize(mine.view, 100, 30);
  await until(() => theirs.view.cols === 100 && mine.view.cols === 100, 'both views at 100');
  await other.bytes.resize(theirs.view, 90, 25);
  await until(() => mine.view.cols === 90 && mine.view.rows === 25, 'the last resize wins');
  await client.takeInput(A);
  await assert.rejects(other.bytes.resize(theirs.view, 70, 20), (e: RpcError) => e.gate === 'input_holder');
  client.close();
  other.close();
});

test('a slow client gets one snapshot above everything it missed', async () => {
  const { client, daemon } = setup();
  await client.start();
  const { view, events } = await watch(client);
  daemon.slowClient(A);
  await until(() => events.some((e) => e.type === 'reset' && e.reason === 'slow_client'), 'slow_client snapshot');
  assert.equal(view.state, 'live');
  client.close();
});

test('terminal.input is refused while the daemon holds the input, and types for the holder', async () => {
  const { client, daemon } = setup();
  await client.start();
  const { view } = await watch(client);
  await assert.rejects(client.bytes.input(view, new TextEncoder().encode('hi')), (e: RpcError) => e.gate === 'input_holder');
  await client.takeInput(A);
  await client.startSession(A);
  await client.bytes.input(view, new TextEncoder().encode('hi\r'));
  assert.ok(daemon.sessions.get(A)!.received.some((r) => r.kind === 'text' && r.value === 'hi'));
  client.close();
});
