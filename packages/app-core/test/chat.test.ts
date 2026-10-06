import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { setup } from '@handline/fake-daemon/helpers';
import { Store, canWithdraw, isOpenAsk, landingLabel, sideOf, timeline } from '../src/index.ts';

const A = 'handline.architect';
const isAgent = (n: string) => n.startsWith('handline.');
const msg = (over: Partial<ChatMessage>): ChatMessage => ({
  id: 1,
  at: '2026-01-01T00:00:00Z',
  state_at: '2026-01-01T00:00:00Z',
  from: 'founder',
  to: A,
  state: 'delivered',
  landing: 'delivered',
  sending_reason: null,
  body: 'hi',
  answer: null,
  dismissed: false,
  delivered_at: null,
  typed_at: null,
  landed_at: null,
  parent: null,
  kind: 'message',
  tasks: [],
  is_task_question: false,
  ...over,
});
const item = (over: Partial<TranscriptItem>): TranscriptItem => ({ id: 1, at: '2026-01-01T00:00:00Z', kind: 'reply', text: 'ok', ...over });

test('the timeline puts messages and transcript items in time order, a message first at the same time', () => {
  const out = timeline(
    [msg({ id: 2, at: '2026-01-01T00:02:00Z' }), msg({ id: 1, at: '2026-01-01T00:00:00Z' })],
    [item({ id: 5, at: '2026-01-01T00:01:00Z' }), item({ id: 6, at: '2026-01-01T00:02:00Z' })],
  );
  assert.deepEqual(
    out.map((e) => `${e.kind}${e.id}`),
    ['message1', 'item5', 'message2', 'item6'],
  );
});

test('who a message is from, whether an ask is open and whether a message can still be taken back', () => {
  const mine = msg({ landing: 'typed' });
  const ask = msg({ from: A, to: 'founder', landing: 'delivered_unanswered' });
  assert.equal(sideOf(mine, A, isAgent), 'person');
  assert.equal(sideOf(ask, A, isAgent), 'agent');
  assert.equal(sideOf(msg({ from: 'handline.auditor' }), A, isAgent), 'other');
  assert.equal(canWithdraw(mine, A), true);
  assert.equal(canWithdraw(msg({ landing: 'delivered' }), A), false);
  assert.equal(canWithdraw(ask, A), false);
  assert.equal(isOpenAsk(ask, A), true);
  assert.equal(isOpenAsk({ ...ask, answer: { id: 9, text: 'x', at: '' } }, A), false);
  assert.equal(isOpenAsk({ ...ask, dismissed: true }, A), false);
  assert.equal(landingLabel('delivered_unanswered'), 'Not answered');
  assert.equal(landingLabel('something_new' as never), 'something_new'); // an open set: unknown states show as sent
});

test('the store knows which chat methods the daemon serves, and subscribes to the chat topic only then', async () => {
  for (const chat of [true, false]) {
    const s = setup({ daemon: { chat } });
    const store = new Store(s.client);
    await store.start();
    assert.equal(store.can('chat.send'), chat);
    assert.equal(store.can('agents.list'), true);
    store.stop();
  }
});
