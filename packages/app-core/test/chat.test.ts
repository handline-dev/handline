import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { setup } from '@handline/fake-daemon/helpers';
import { Store, behindCount, noticeOf, takeBack, isOpenAsk, landingLabel, sendNote, sideOf, taskRow, timeline } from '../src/index.ts';

const A = 'handline.architect';
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

test("a reply with the same words as a message is still shown: the agent answering 'ok' to 'ok'", () => {
  const out = timeline([msg({ id: 3, body: 'ok' })], [item({ id: 8, text: 'ok' })]);
  assert.deepEqual(
    out.map((e) => `${e.kind}${e.id}`),
    ['message3', 'item8'],
  );
});

test('who a message is from, whether an ask is open', () => {
  const mine = msg({ landing: 'typed', from: 'Handline desktop' });
  const ask = msg({ from: A, to: 'founder', state: 'queued', landing: 'delivered_unanswered' });
  assert.equal(sideOf(mine, A, 'Handline desktop'), 'person');
  assert.equal(sideOf(ask, A, 'Handline desktop'), 'agent');
  assert.equal(sideOf(msg({ from: 'handline.auditor' }), A, 'Handline desktop'), 'other');
  assert.equal(isOpenAsk(ask, A), true);
  assert.equal(isOpenAsk({ ...ask, answer: { id: 9, text: 'x', at: '' } }, A), false);
  assert.equal(isOpenAsk({ ...ask, dismissed: true }, A), false);
  assert.equal(isOpenAsk({ ...ask, state: 'delivered' }, A), false); // a report that asked nothing: a reply is a say under it
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

const taskItem = (event: string, extra: object = {}): TranscriptItem =>
  ({
    id: 1,
    at: '2026-10-12T10:00:00Z',
    kind: 'task_event',
    task_event: { event, task: 7, request: null, evidence: null, ...extra },
  }) as TranscriptItem;
const titles = new Map([[7, 'Fix the tray']]);

test('a task line says added, edited, done or deleted', () => {
  assert.equal(taskRow(taskItem('opened'), titles).label, 'Task added');
  assert.equal(taskRow(taskItem('changed'), titles).label, 'Task edited');
  assert.equal(taskRow(taskItem('done'), titles).label, 'Task done');
  assert.equal(taskRow(taskItem('withdrawn'), titles).label, 'Task deleted');
});

test('a task line carries the task, its title and only the change or evidence', () => {
  assert.deepEqual(taskRow(taskItem('changed', { change: 'priority 2 -> 1' }), titles), {
    label: 'Task edited',
    task: 7,
    title: 'Fix the tray',
    detail: 'priority 2 -> 1',
  });
  assert.equal(taskRow(taskItem('withdrawn', { evidence: 'duplicate' })).detail, 'duplicate');
  assert.equal(taskRow(taskItem('opened'), titles).detail, null);
  assert.equal(taskRow(taskItem('opened')).title, null);
});

const pm = (o: Partial<ChatMessage>) =>
  ({ id: 5, from: 'Handline desktop', to: 'handline.x', landing: 'queued', state: 'queued', ...o }) as ChatMessage;

test('only the person\'s own words are on their side: the CLI, this device, to whichever agent', () => {
  assert.equal(sideOf(pm({}), A, 'Handline desktop'), 'person');
  assert.equal(sideOf(pm({ from: 'human' }), A, 'Handline desktop'), 'person');
  assert.equal(sideOf(pm({ from: 'handline.y' }), A, 'Handline desktop'), 'other');
  assert.equal(sideOf(pm({ from: 'task-board', to: A }), A, 'Handline desktop'), 'other');
  assert.equal(sideOf(pm({ from: 'schedule', to: A }), A, ''), 'other');
});

test('the person\'s message says sending, waiting behind N, queued or typed, and nothing once delivered or failed', () => {
  assert.equal(sendNote(pm({ id: -1 }), 0), 'Sending…');
  assert.equal(sendNote(pm({}), 2), 'Waiting behind 2');
  assert.equal(sendNote(pm({}), 0), 'Queued');
  assert.equal(sendNote(pm({ landing: 'typed' }), 0), 'Typed, not read yet');
  assert.equal(sendNote(pm({ landing: 'delivered' }), 0), null);
  assert.equal(sendNote(pm({ landing: 'answered' }), 0), null);
  assert.equal(sendNote(pm({ state: 'failed', id: -1 }), 0), null);
});

test('behindCount counts the earlier messages still queued to the same agent', () => {
  const a = pm({ id: 1 });
  const b = pm({ id: 2, landing: 'delivered' });
  const c = pm({ id: 3 });
  assert.equal(behindCount([a, b, c], c), 1);
  assert.equal(behindCount([a, b, c], a), 0);
});

test('a message can be taken back until it is answered, and the agent is asked first once it has it', () => {
  assert.deepEqual(takeBack(pm({ landing: 'queued' })), { confirm: false });
  assert.deepEqual(takeBack(pm({ landing: 'typed' })), { confirm: false });
  assert.deepEqual(takeBack(pm({ landing: 'delivered' })), { confirm: true });
  assert.deepEqual(takeBack(pm({ landing: 'delivered_unanswered' })), { confirm: true });
  assert.equal(takeBack(pm({ landing: 'answered' })), null);
  assert.equal(takeBack(pm({ landing: 'withdrawn' })), null);
  const r = (state: 'queued' | 'sent' | 'confirmed') => pm({ landing: 'typed', receipt: { state, uuid: 'u' } as never });
  assert.deepEqual(takeBack(r('queued')), { confirm: false });
  assert.deepEqual(takeBack(r('sent')), { confirm: true });
});

test('the task board\'s words are task cards or hidden notices, in the agent\'s own chat and in the All view', () => {
  const news = pm({ id: 7, from: 'task-board', to: A, body: 'T3492 done by handline.pair: -' });
  const delivery = pm({ id: 8, from: 'task-board', to: A, body: '#28387 to handline.pair was written to its session' });
  assert.deepEqual(noticeOf(news), { kind: 'task', task: 3492 });
  assert.deepEqual(noticeOf(delivery), { kind: 'delivery' });
  assert.equal(noticeOf(pm({ from: 'human', body: 'T1 done by me' })), null);
  const kinds = (scope: 'mine' | 'all') => timeline([news, delivery], [], scope).map((e) => `${e.kind}${e.id}`);
  assert.deepEqual(kinds('mine'), ['note7']);
  assert.deepEqual(kinds('all'), ['note7', 'message8']);
  assert.equal(sideOf(delivery, A, 'Handline desktop'), 'other');
});
