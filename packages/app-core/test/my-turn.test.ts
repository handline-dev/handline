import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentView, InboxItem, Task } from '@handline/client';
import { inboxGroup, myTurn } from '../src/my-turn.ts';

const item = (o: Partial<InboxItem>): InboxItem =>
  ({
    id: 1,
    kind: 'ask',
    agent: 'a',
    title: 't',
    body: '',
    evidence: null,
    task: null,
    created: '2026-10-15T00:00:00Z',
    read: false,
    answered: false,
    options: [],
    prompt: null,
    ...o,
  }) as InboxItem;
const task = (o: Partial<Task>): Task =>
  ({ id: 1, title: 'T', state: 'doing', priority: 2, to: 'a', time: null, report: null, ...o }) as Task;
const agent = (o: Partial<AgentView>): AgentView => ({ name: 'a', needs_you: null, task: null, ...o }) as AgentView;
const perm = {
  id: 'p1',
  agent: 'a',
  kind: 'permission',
  tool: 'Bash',
  command: 'rm -rf dist',
  reason: null,
  lines: [],
  options: [
    { key: '1', label: 'Yes', answer: 'yes' },
    { key: '2', label: 'No', answer: 'no' },
  ],
  since: '2026-10-15T00:00:00Z',
};

test('cards sort by priority, then soonest deadline (none last), then oldest', () => {
  const tasks = [
    task({ id: 1, priority: 2, time: { end: '2026-10-15T10:00:00Z' } as Task['time'] }),
    task({ id: 2, priority: 1 }),
    task({ id: 3, priority: 2, time: { end: '2026-10-15T09:00:00Z' } as Task['time'] }),
    task({ id: 4, priority: 2 }),
  ];
  const items = [1, 2, 3, 4].map((n) => item({ id: n, task: n, kind: 'decision', created: `2026-10-15T0${5 - n}:00:00Z` }));
  assert.deepEqual(
    myTurn({ items, agents: [], tasks }).map((c) => c.task),
    [2, 3, 1, 4],
  );
});

test('a permission item offers Allow then Deny; read items are not cards', () => {
  const cards = myTurn({
    items: [item({ id: 5, kind: 'prompt', prompt: perm as InboxItem['prompt'] }), item({ id: 6, read: true })],
    agents: [],
    tasks: [],
  });
  assert.equal(cards.length, 1);
  assert.deepEqual(
    cards[0]!.actions.slice(0, 2).map((a) => a.type),
    ['allow', 'deny'],
  );
});

test('a dialog needs_you has only Open terminal, and a permission one is not doubled by its item', () => {
  const dialog = agent({ name: 'd', needs_you: { kind: 'dialog', since: '2026-10-15T00:00:00Z', text: 'x', source: 'hook' } });
  const asks = agent({ name: 'a', needs_you: { kind: 'permission', since: '2026-10-15T00:00:00Z', text: null, source: 'hook' } });
  const cards = myTurn({ items: [item({ kind: 'prompt', prompt: perm as InboxItem['prompt'] })], agents: [dialog, asks], tasks: [] });
  assert.equal(cards.filter((c) => c.agent === 'a').length, 1);
  assert.deepEqual(
    cards.find((c) => c.agent === 'd')!.actions.map((a) => a.type),
    ['terminal'],
  );
});

test('a reported task is an Accept card with Accept first', () => {
  const cards = myTurn({
    items: [],
    agents: [],
    tasks: [task({ id: 9, from: 'human', state: 'review', report: { by: 'a', evidence: 'ok', at: '2026-10-15T00:00:00Z' } })],
  });
  assert.deepEqual(
    cards[0]!.actions.map((a) => a.type),
    ['accept', 'reject', 'open'],
  );
});

test('inboxGroup: decision and ask are decisions, a permission prompt is an approval, the rest is FYI', () => {
  const g = (o: Partial<InboxItem>) => inboxGroup(item(o));
  assert.equal(g({ kind: 'decision' }), 'decisions');
  assert.equal(g({ kind: 'ask' }), 'decisions');
  assert.equal(g({ kind: 'prompt', prompt: { kind: 'permission' } as InboxItem['prompt'] }), 'approvals');
  assert.equal(g({ kind: 'ask', prompt: { kind: 'permission' } as InboxItem['prompt'] }), 'approvals');
  for (const kind of ['say', 'result', 'report', 'block', 'refused']) assert.equal(g({ kind }), 'fyi');
});

test('a report that answers to an agent is no Accept card; one that answers to the caller is', () => {
  const report = { by: 'a', evidence: 'ok', at: '2026-10-15T00:00:00Z' };
  const tasks = [
    task({ id: 1, from: 'b', state: 'review', report }),
    task({ id: 2, from: 'me', state: 'review', report }),
    task({ id: 3, from: 'human', state: 'review', report }),
  ];
  assert.deepEqual(
    myTurn({ items: [], agents: [], tasks, me: 'me' })
      .map((c) => c.task)
      .sort(),
    [2, 3],
  );
});

test('has_dialog without needs_you is a dialog card with only Open terminal', () => {
  const cards = myTurn({ items: [], agents: [agent({ has_dialog: true, state_detail: 'Trust this folder?' })], tasks: [] });
  assert.equal(cards[0]!.kind, 'dialog');
  assert.deepEqual(
    cards[0]!.actions.map((a) => a.type),
    ['terminal'],
  );
});

test('a card about a task is titled with the task and says who asks; a decision has Done beside Reply', () => {
  const cards = myTurn({
    items: [item({ id: 7, task: 9, kind: 'decision', title: 'Decision needed: the release', body: 'Decision needed: the release\nPick A or B.', agent: 'human' })],
    agents: [],
    tasks: [task({ id: 9, title: 'Pick login timeout fix', from: 'handline.pc' } as Partial<Task>)],
  });
  assert.equal(cards[0]!.title, 'Pick login timeout fix');
  assert.match(cards[0]!.detail, /Pick A or B/);
  assert.equal(cards[0]!.from, 'handline.pc');
  assert.deepEqual(cards[0]!.actions.slice(0, cards[0]!.primary).map((a) => a.type), ['answer', 'done']);
});
