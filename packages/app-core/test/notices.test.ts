// The notice model: the three lanes, merging, how long a toast stays, the list and its unread count, and events from the stream and the feed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Event as WireEvent } from '@handline/client';
import { MAX_LIST, NEWS_MS, Notices, aboutOf, eventText, isMyTurn, targetOf } from '#src/index.ts';

const ev = (cursor: number, topic: string, type: string, extra: Partial<WireEvent> = {}) =>
  ({ cursor, topic, type, at: '', actor: null, agent: null, title: null, target: null, data: {}, ...extra }) as WireEvent;

function clock() {
  let t = 1_000_000;
  return { now: () => t, tick: (ms: number) => (t += ms) };
}

test('news goes after 6 s; a need and a failure stay until opened or closed; all live on in the list', () => {
  const c = clock();
  const n = new Notices(c.now);
  n.push('ok', 'Applied');
  n.push('fail', 'Not applied: busy');
  n.push('need', 'a.b is blocked');
  assert.equal(n.toasts().length, 3);
  c.tick(NEWS_MS - 1);
  assert.equal(n.toasts().length, 3);
  c.tick(2);
  assert.deepEqual(
    n.toasts().map((x) => x.text),
    ['a.b is blocked', 'Not applied: busy'],
  );
  c.tick(3_600_000);
  assert.equal(n.toasts().length, 2);
  assert.equal(n.list().length, 3);
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

test('the pointer or the keyboard focus stops the clock of a toast, which runs on with what was left once both are gone', () => {
  const c = clock();
  const n = new Notices(c.now);
  const a = n.push('ok', 'x');
  c.tick(4000);
  n.hold(a.id, 'pointer');
  n.hold(a.id, 'focus');
  c.tick(60_000);
  assert.equal(n.toasts().length, 1);
  n.release(a.id, 'pointer');
  c.tick(60_000);
  assert.equal(n.toasts().length, 1, 'focus still holds it');
  n.release(a.id, 'focus');
  c.tick(1900);
  assert.equal(n.toasts().length, 1);
  c.tick(200);
  assert.equal(n.toasts().length, 0);
});

test('while the window is hidden or not focused no news clock runs, and one that comes meanwhile waits too', () => {
  const c = clock();
  const n = new Notices(c.now);
  n.push('ok', 'before');
  c.tick(5000);
  n.setAway(true);
  n.push('ok', 'while away');
  c.tick(600_000);
  assert.equal(n.toasts().length, 2);
  n.setAway(false);
  c.tick(900);
  assert.equal(n.toasts().length, 2);
  c.tick(200);
  assert.deepEqual(
    n.toasts().map((x) => x.text),
    ['while away'],
  );
  c.tick(NEWS_MS);
  assert.equal(n.toasts().length, 0);
});

test('opening closes only that toast and reads that notice; closing leaves it unread; dismissing spares a need', () => {
  const n = new Notices(clock().now);
  const a = n.push('need', 'a.b is blocked');
  const b = n.push('need', 'Account x is out');
  const c = n.push('ok', 'Applied');
  const f = n.push('fail', 'Not applied');
  n.open(a.id);
  assert.deepEqual(
    n.toasts().map((x) => x.id),
    [f.id, c.id, b.id],
  );
  assert.equal(n.unread(), 3);
  n.close(b.id);
  assert.equal(n.unread(), 3, 'closed, still unread');
  assert.equal(n.list().length, 4);
  const d = n.push('need', 'again');
  n.dismissToasts();
  assert.deepEqual(
    n.toasts().map((x) => x.id),
    [d.id],
  );
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

test('removing the read ones keeps the unread', () => {
  const n = new Notices(clock().now);
  n.push('info', 'a');
  n.markAllRead();
  n.push('fail', 'b');
  n.removeRead();
  assert.deepEqual(
    n.list().map((x) => x.text),
    ['b'],
  );
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
    [['a.b is blocked', 1]],
  );
  assert.equal(eventText(ev(4, 'notifications', 'account_out', { data: { account: 'alpha' } })), 'Account alpha is out of usage');
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

test('the listeners hear when a toast runs out, so a screen that draws on change takes it down', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const c = clock();
  const n = new Notices(c.now);
  let heard = 0;
  n.subscribe(() => heard++);
  n.push('ok', 'x');
  const after = heard;
  c.tick(NEWS_MS + 1);
  t.mock.timers.tick(NEWS_MS + 2);
  assert.ok(heard > after);
  assert.equal(n.toasts().length, 0);
});

test('an event carries what it is about, and that says the page it opens', () => {
  const n = new Notices();
  n.takeEvent(ev(1, 'notifications', 'task_done', { agent: 'a.b', data: { task: 7, task_title: 'Fix it', result: 'PASS' } }));
  n.takeEvent(ev(2, 'notifications', 'task_done', { agent: 'a.c', data: { task: 8 } }));
  n.takeEvent(ev(3, 'notifications', 'stall', { agent: 'a.b' }));
  n.takeEvent(ev(4, 'notifications', 'account_out', { data: { account: 'alpha' } }));
  n.takeEvent(ev(5, 'hr', 'hr.settled', { agent: 'a.d', data: { proposal: { id: 12 } } }));
  assert.deepEqual(
    n.list().map((x) => [x.level, x.about, targetOf(x)]),
    [
      ['info', { agent: 'a.d', proposal: 12 }, '#/team/hr/12'],
      ['need', { account: 'alpha' }, '#/accounts/alpha'],
      ['need', { agent: 'a.b' }, '#/agents/a.b'],
      ['info', { agent: 'a.c', task: 8 }, '#/tasks/8'],
      ['info', { agent: 'a.b', task: 7, title: 'Fix it', result: 'PASS' }, '#/tasks/7'],
    ],
  );
  assert.equal(targetOf(n.push('fail', 'Not applied')), null);
});

test('with more than three on screen, news gives way before what needs the person', () => {
  const n = new Notices(clock().now);
  n.push('need', 'blocked');
  for (const t of ['a', 'b', 'c']) n.push('ok', t);
  assert.deepEqual(
    n.toasts().map((x) => x.text),
    ['c', 'b', 'blocked'],
  );
});

test('a finished task says whether the founder gave it, and says nothing when the daemon did not', () => {
  const done = (data: Record<string, unknown>) =>
    aboutOf(ev(1, 'notifications', 'task_done', { agent: 'a.b', data: { task: 7, ...data } }));
  assert.equal(done({ from_founder: true })?.fromFounder, true);
  assert.equal(done({ from_founder: false })?.fromFounder, false);
  assert.equal('fromFounder' in done({})!, false);
  assert.equal('fromFounder' in done({ from_founder: 'yes' })!, false);
});
