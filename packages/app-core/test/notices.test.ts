// The notice model: the three lanes, merging, how long a toast stays, the list and its unread count, and events from the stream and the feed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Event as WireEvent } from '@handline/client';
import { MAX_LIST, Notices, eventText, isMyTurn } from '#src/index.ts';

const ev = (cursor: number, topic: string, type: string, extra: Partial<WireEvent> = {}) =>
  ({ cursor, topic, type, at: '', actor: null, agent: null, title: null, target: null, data: {}, ...extra }) as WireEvent;

function clock() {
  let t = 1_000_000;
  return { now: () => t, tick: (ms: number) => (t += ms) };
}

test('a success stays 3 s and a failure 8 s, then both live on in the list', () => {
  const c = clock();
  const n = new Notices(c.now);
  n.push('ok', 'Applied');
  n.push('fail', 'Not applied: busy');
  assert.equal(n.toasts().length, 2);
  c.tick(3001);
  assert.deepEqual(
    n.toasts().map((x) => x.text),
    ['Not applied: busy'],
  );
  c.tick(5000);
  assert.equal(n.toasts().length, 0);
  assert.equal(n.list().length, 2);
});

test('the same notice within a minute is one line counted x2, and after a minute it is a new line', () => {
  const c = clock();
  const n = new Notices(c.now);
  n.push('fail', 'start: busy');
  c.tick(30_000);
  n.push('fail', 'start: busy');
  assert.equal(n.list().length, 1);
  assert.equal(n.list()[0]!.count, 2);
  c.tick(61_000);
  n.push('fail', 'start: busy');
  assert.equal(n.list().length, 2);
});

test('at most three toasts stack, the oldest dropped from the screen but not the list', () => {
  const n = new Notices(clock().now);
  for (const t of ['a', 'b', 'c', 'd']) n.push('ok', t);
  assert.deepEqual(
    n.toasts().map((x) => x.text),
    ['d', 'c', 'b'],
  );
  assert.equal(n.list().length, 4);
});

test('a held toast does not run out and runs its time out again once released', () => {
  const c = clock();
  const n = new Notices(c.now);
  const a = n.push('fail', 'x');
  c.tick(5000);
  n.hold(a.id);
  c.tick(60_000);
  assert.equal(n.toasts().length, 1);
  n.release(a.id);
  c.tick(2900);
  assert.equal(n.toasts().length, 1);
  c.tick(200);
  assert.equal(n.toasts().length, 0);
});

test('the list keeps the last 100 and counts the unread until they are marked read', () => {
  const n = new Notices(clock().now);
  for (let i = 0; i < MAX_LIST + 5; i++) n.push('info', `n${i}`);
  assert.equal(n.list().length, MAX_LIST);
  assert.equal(n.unread(), MAX_LIST);
  n.markAllRead();
  assert.equal(n.unread(), 0);
  n.push('info', 'new');
  assert.equal(n.unread(), 1);
});

test('dismissing hides the toasts and keeps the list', () => {
  const n = new Notices(clock().now);
  n.push('fail', 'x');
  n.dismissToasts();
  assert.equal(n.latestFailure(), null);
  assert.equal(n.list().length, 1);
});

test('an event is a line unless it asks for a decision, and one cursor is taken once', () => {
  const n = new Notices(clock().now);
  assert.equal(isMyTurn(ev(1, 'notifications', 'prompt_asked')), true);
  assert.equal(isMyTurn(ev(1, 'hr', 'hr.raised')), true);
  n.takeEvent(ev(1, 'notifications', 'prompt_asked'));
  n.takeEvent(ev(2, 'notifications', 'stall', { agent: 'a.b' }));
  n.takeEvent(ev(2, 'notifications', 'stall', { agent: 'a.b' }));
  n.takeEvent(ev(3, 'notifications', 'unknown_kind'));
  assert.deepEqual(
    n.list().map((x) => [x.text, x.count]),
    [['a.b stalled', 1]],
  );
  assert.equal(eventText(ev(4, 'notifications', 'account_out', { data: { account: 'alpha' } })), 'Account alpha is out');
});

test('agent events are filed under team, results under system', () => {
  const t = 0;
  const n = new Notices(() => t);
  n.takeEvent({ cursor: 1, topic: 'notifications', type: 'task_done', agent: 'a', title: 'a done', data: {} } as unknown as WireEvent);
  n.push('ok', 'Applied');
  assert.deepEqual(
    n.list().map((x) => [x.text, x.lane]),
    [
      ['Applied', 'system'],
      ['a done', 'team'],
    ],
  );
});

test('the listeners hear when a toast runs out, so a screen that draws on change takes it down', async () => {
  const n = new Notices();
  let heard = 0;
  n.subscribe(() => heard++);
  n.push('ok', 'x');
  const after = heard;
  await new Promise((r) => setTimeout(r, 3100));
  assert.ok(heard > after);
  assert.equal(n.toasts().length, 0);
});

test('a finished task keeps its closer, number, title and result; other events carry nothing extra', () => {
  const n = new Notices();
  n.takeEvent(ev(1, 'notifications', 'task_done', { agent: 'a.b', data: { task: 7, task_title: 'Fix it', result: 'PASS' } }));
  n.takeEvent(ev(2, 'notifications', 'task_done', { agent: 'a.c', data: { task: 8 } }));
  n.takeEvent(ev(3, 'notifications', 'stall', { agent: 'a.b' }));
  assert.deepEqual(
    n.list().map((x) => x.about),
    [undefined, { agent: 'a.c', task: 8 }, { agent: 'a.b', task: 7, title: 'Fix it', result: 'PASS' }],
  );
});
