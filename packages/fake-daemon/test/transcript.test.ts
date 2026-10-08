import { test } from 'node:test';
import assert from 'node:assert/strict';
import { type Event, type RpcError, type TranscriptEvent } from '@handline/client';
import { setup, until } from '../src/helpers.ts';

const ARCH = 'handline.auditor';
const kinds = (es: { kind: string }[]) => es.map((e) => e.kind);

test('the seeded transcript has every kind, in order, and seq goes up by one', async () => {
  const { client } = setup();
  await client.start();
  const r = await client.call('transcript.events', { agent: ARCH });
  assert.deepEqual(
    new Set(kinds(r.events)),
    new Set(['turn_start', 'text', 'tool_use', 'tool_result', 'permission', 'permission_answer', 'turn_end', 'error']),
  );
  assert.deepEqual(
    r.events.map((e) => e.seq),
    r.events.map((_, i) => i + 1),
  );
  assert.equal(r.last_seq, r.events.length);
  assert.equal(r.working.turn_running, false);
  assert.equal((await client.call('transcript.events', { agent: ARCH, since_seq: r.last_seq })).events.length, 0);
  assert.deepEqual(
    (await client.call('transcript.events', { agent: ARCH, since_seq: 2, limit: 2 })).events.map((e) => e.seq),
    [3, 4],
  );
  assert.deepEqual(
    (await client.call('transcript.events', { agent: ARCH, before_seq: 4, limit: 2 })).events.map((e) => e.seq),
    [2, 3],
  );
  client.close();
});

test('a terminal agent is unsupported, an unknown one not found, and bad paging bad_request', async () => {
  const { client } = setup();
  await client.start();
  await assert.rejects(
    client.call('transcript.events', { agent: 'handline.term-build' }),
    (e: RpcError) => e.code === 'refused' && e.gate === 'unsupported',
  );
  await assert.rejects(client.call('transcript.events', { agent: 'no.such' }), (e: RpcError) => e.code === 'not_found');
  await assert.rejects(
    client.call('transcript.events', { agent: ARCH, since_seq: 1, before_seq: 3 }),
    (e: RpcError) => e.data?.field === 'since_seq',
  );
  await assert.rejects(client.call('transcript.events', { agent: ARCH, limit: 501 }), (e: RpcError) => e.data?.field === 'limit');
  client.close();
});

test('the permission card is answered by prompt.answer, and the stream says so and ends the turn', async () => {
  const { client, daemon } = setup();
  await client.start();
  await client.subscribe(['transcript']);
  const got: TranscriptEvent[] = [];
  client.onEvent((e: Event) => e.topic === 'transcript' && got.push(e.data as unknown as TranscriptEvent));
  const turn = 'turn-live';
  daemon.transcript.push(ARCH, { kind: 'turn_start', turn, turn_start: { message: turn, from: 'founder', text: 'push' } });
  const id = daemon.transcript.ask(ARCH, turn, 'Bash', 'git push');
  await until(() => got.some((e) => e.kind === 'permission'), 'the permission event');
  const open = (await client.call('prompt.list', { agent: ARCH })).prompts;
  assert.deepEqual(
    open.map((p) => p.id),
    [id],
  );
  await client.call('prompt.answer', { agent: ARCH, prompt: id, choice: 'allow', request_id: 'r1' });
  await until(() => got.some((e) => e.kind === 'turn_end' && e.turn === turn), 'the end of the turn');
  assert.deepEqual(kinds(got), ['turn_start', 'tool_use', 'permission', 'permission_answer', 'tool_result', 'turn_end']);
  assert.deepEqual(
    got.map((e) => e.seq),
    got.map((_, i) => got[0]!.seq + i),
  );
  assert.equal(got[3]!.permission_answer!.by, 'person');
  assert.deepEqual((await client.call('prompt.list', { agent: ARCH })).prompts, []);
  client.close();
});

test('a client that reconnects is replayed what it missed, and the seq has no hole', async () => {
  const { client, daemon, net } = setup();
  await client.start();
  await client.subscribe(['transcript']);
  const seen: number[] = [];
  client.onEvent((e: Event) => seen.push((e.data as unknown as TranscriptEvent).seq));
  const last = (await client.call('transcript.events', { agent: ARCH })).last_seq;
  daemon.transcript.push(ARCH, { kind: 'text', text: 'before the drop' });
  await until(() => seen.length === 1, 'the live event');
  net.down = true;
  daemon.dropConnections();
  await until(() => client.state === 'reconnecting', 'drop');
  daemon.transcript.push(ARCH, { kind: 'text', text: 'during the outage' });
  net.down = false;
  await until(() => seen.length === 2, 'the replay');
  assert.deepEqual(seen, [last + 1, last + 2]);
  client.close();
});

test('a daemon without the transcript lists no method, says so in hello, and refuses the topic', async () => {
  const { client } = setup({ daemon: { transcript: false } });
  const hello = await client.start();
  assert.ok(!hello.methods.some((m) => m.name === 'transcript.events'));
  assert.ok(!hello.capabilities.transcript);
  await assert.rejects(client.subscribe(['transcript']), (e: RpcError) => e.data?.field === 'topics');
  client.close();
});
