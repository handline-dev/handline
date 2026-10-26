import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { Task } from '@handline/client';
import { CARD_H, CARD_W, GAP_Y, chainOf, columnGraph } from '#src/task-columns.ts';

const NOW = Date.parse('2026-06-10T12:00:00Z');
const task = (id: number, over: Partial<Task> = {}): Task => ({
  id,
  from: 'human',
  to: 'team.a',
  title: `task ${id}`,
  state: 'queued',
  time_box: null,
  parent: null,
  created: '2026-06-01T00:00:00Z',
  delivered: null,
  done: null,
  hold_reason: null,
  block_reason: null,
  evidence: null,
  ...over,
});
const g = (ts: Task[], doneShown?: number) => columnGraph(ts, { me: 'human', now: NOW, doneShown });
const card = (gr: ReturnType<typeof g>, id: number) => gr.cards.find((c) => c.id === id)!;

test('the person is the first column, then the agents by name, and every running task sits on the now row', () => {
  const gr = g([task(1, { to: 'team.b', state: 'doing' }), task(2, { to: 'team.a', state: 'doing' }), task(3, { to: 'human' })]);
  assert.deepEqual(
    gr.columns.map((c) => c.label),
    ['You', 'a', 'b'],
  );
  assert.equal(card(gr, 1).y, gr.nowY);
  assert.equal(card(gr, 2).y, gr.nowY);
  assert.equal(card(gr, 3).row, 'next');
  assert.equal(card(gr, 3).holder, 'you');
});

test('queued tasks go below by priority, an agent with nothing running is idle, and a person with no tasks has no column', () => {
  const gr = g([task(1, { priority: 3 }), task(2, { priority: 1 }), task(3, { to: 'team.b', state: 'doing' })]);
  assert.ok(card(gr, 2).y < card(gr, 1).y);
  assert.equal(card(gr, 2).y, gr.nowY + CARD_H + GAP_Y);
  assert.deepEqual(
    gr.columns.map((c) => c.idle),
    [true, false],
  );
});

test('finished tasks sit above in day bands, the newest nearest, capped with the rest counted', () => {
  const done = (id: number, at: string) => task(id, { state: 'done', done: at });
  const gr = g([
    done(1, '2026-06-10T09:00:00Z'),
    done(2, '2026-06-10T10:00:00Z'),
    done(3, '2026-06-08T10:00:00Z'),
    task(4, { state: 'doing' }),
  ]);
  assert.deepEqual(
    gr.bands.map((b) => b.label),
    ['2 days ago', 'Today'],
  );
  assert.ok(card(gr, 3).y < card(gr, 1).y && card(gr, 1).y < card(gr, 2).y && card(gr, 2).y < gr.nowY);
  const capped = g([done(1, '2026-06-10T09:00:00Z'), done(2, '2026-06-10T10:00:00Z')], 1);
  assert.equal(capped.moreDone, 1);
  assert.deepEqual(
    capped.cards.map((c) => c.id),
    [2],
  );
});

test('solid arrows join a task to what it waits on across columns, dashed ones to the next of the same agent', () => {
  const gr = g([task(1, { state: 'doing' }), task(2, { to: 'team.b', state: 'blocked', blocked_by: [1] }), task(3)]);
  assert.deepEqual(
    gr.edges.filter((e) => e.kind === 'waits').map((e) => [e.from, e.to]),
    [[1, 2]],
  );
  assert.deepEqual(
    gr.edges.filter((e) => e.kind === 'next').map((e) => [e.from, e.to]),
    [[1, 3]],
  );
  assert.equal(card(gr, 2).waitsOn, 'blocked by T1');
  const e = gr.edges.find((x) => x.kind === 'waits')!;
  assert.equal(e.points[0]!.x, card(gr, 1).x + CARD_W / 2);
});

test('a task that waits on the person says so, and a click lights the whole chain', () => {
  const ask = { id: 9, from: 'team.a', to: 'human', state: 'landed', at: '2026-06-10T00:00:00Z' };
  const gr = g([task(1, { state: 'doing', requests: [ask] }), task(2, { blocked_by: [1] }), task(3, { blocked_by: [2] }), task(4)]);
  assert.equal(card(gr, 1).onYou, true);
  assert.equal(card(gr, 1).waitsOn, 'waiting on you');
  assert.deepEqual([...chainOf(gr.edges, 2)].sort(), [1, 2, 3]);
  assert.deepEqual([...chainOf(gr.edges, 4)], [4]);
});
