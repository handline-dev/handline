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
    tasks: [task({ id: 9, state: 'reported', report: { by: 'a', evidence: 'ok', at: '2026-10-15T00:00:00Z' } })],
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
