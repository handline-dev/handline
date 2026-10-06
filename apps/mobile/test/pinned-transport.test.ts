import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pinnedTransport } from '../src/pinned-transport.ts';
import type { PinnedSocketNative } from '../modules/pinned-socket/index.ts';

/** The native module's contract, in memory: events by connection id, a pin that must match. */
function fakeNative(goodPin: string) {
  const subs = {
    message: new Set<(e: { id?: string; data?: string }) => void>(),
    close: new Set<(e: { id?: string; data?: string }) => void>(),
  };
  const sent: string[] = [];
  const native: PinnedSocketNative = {
    async connect(_id, _url, pin) {
      if (pin !== goodPin) throw new Error('cancelled');
    },
    send(_id, text) {
      sent.push(text);
    },
    close(id) {
      for (const f of subs.close) f({ id });
    },
    addListener(event: 'message' | 'close', fn: (e: { id?: string; data?: string }) => void) {
      subs[event].add(fn);
      return { remove: () => subs[event].delete(fn) };
    },
  } as PinnedSocketNative;
  const emit = (event: 'message' | 'close', e: object) => {
    for (const f of [...subs[event]]) f(e);
  };
  return { native, sent, emit, subs };
}

test('a connection with the right pin carries messages both ways and close reaches onclose', async () => {
  const f = fakeNative('ab');
  const t = await pinnedTransport(f.native, 'wss://h:1/rpc', 'ab');
  const got: string[] = [];
  t.onmessage = (m) => got.push(m);
  f.emit('message', { id: 'other', data: 'not mine' });
  f.emit('message', { id: 'c1', data: 'hi' });
  t.send('out');
  assert.deepEqual(got, ['hi']);
  assert.deepEqual(f.sent, ['out']);
  let closed = 0;
  t.onclose = () => closed++;
  f.emit('close', { id: 'c1' });
  assert.equal(closed, 1);
  assert.equal(f.subs.message.size + f.subs.close.size, 0, 'listeners are removed');
  t.send('after close'); // dropped, not sent
  assert.deepEqual(f.sent, ['out']);
});

test('messages that arrive before onmessage is set are delivered once it is', async () => {
  const f = fakeNative('ab');
  const t = await pinnedTransport(f.native, 'wss://h:1/rpc', 'ab');
  f.emit('message', { id: 'c2', data: 'first' });
  f.emit('message', { id: 'c2', data: 'second' });
  const got: string[] = [];
  t.onmessage = (m) => got.push(m);
  assert.deepEqual(got, ['first', 'second']);
});

test('a wrong pin rejects with the address and leaves no listener behind', async () => {
  const f = fakeNative('ab');
  await assert.rejects(pinnedTransport(f.native, 'wss://h:1/rpc', 'cd'), /cannot connect to wss:\/\/h:1\/rpc: cancelled/);
  assert.equal(f.subs.message.size + f.subs.close.size, 0);
});
