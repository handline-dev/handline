import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentView, InboxItem, Task } from '@handline/client';
import { inboxGroup, messageRows, myTurn, reviewPictures, changeAsked, turnAgents } from '#src/my-turn.ts';

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

test('inboxGroup: decision and ask are decisions, a permission prompt is an approval, the rest is FYI', () => {
  const g = (o: Partial<InboxItem>) => inboxGroup(item(o));
  assert.equal(g({ kind: 'decision' }), 'decisions');
  assert.equal(g({ kind: 'ask' }), 'decisions');
  assert.equal(g({ kind: 'prompt', prompt: { kind: 'permission' } as InboxItem['prompt'] }), 'approvals');
  assert.equal(g({ kind: 'ask', prompt: { kind: 'permission' } as InboxItem['prompt'] }), 'approvals');
  for (const kind of ['say', 'result', 'report', 'block', 'refused']) assert.equal(g({ kind }), 'fyi');
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
    items: [
      item({
        id: 7,
        task: 9,
        kind: 'decision',
        title: 'Decision needed: the release',
        body: 'Decision needed: the release\nPick A or B.',
        agent: 'human',
      }),
    ],
    agents: [],
    tasks: [task({ id: 9, title: 'Pick login timeout fix', from: 'handline.pc' } as Partial<Task>)],
  });
  assert.equal(cards[0]!.title, 'Pick login timeout fix');
  assert.match(cards[0]!.detail, /Pick A or B/);
  assert.equal(cards[0]!.from, 'handline.pc');
  assert.deepEqual(
    cards[0]!.actions.slice(0, cards[0]!.primary).map((a) => a.type),
    ['answer', 'done'],
  );
});

test('an item with fix open_terminal has Open terminal as its first button, before the reply', () => {
  const [c] = myTurn({ items: [item({ id: 9, kind: 'decision', fix: 'open_terminal' })], agents: [], tasks: [] });
  assert.deepEqual(
    c!.actions.slice(0, 2).map((a) => a.type),
    ['terminal', 'answer'],
  );
  assert.equal(c!.actions[0]!.type === 'terminal' && c!.actions[0]!.agent, 'a');
  assert.equal(c!.primary, 2);
  const [plain] = myTurn({ items: [item({ id: 9, kind: 'decision' })], agents: [], tasks: [] });
  assert.equal(
    plain!.actions.some((a) => a.type === 'terminal'),
    false,
  );
});

test('a decision on a task tagged review offers Approve and Ask for change in place of Done', () => {
  const decision = item({ id: 7, kind: 'decision', task: 5 });
  const [plain] = myTurn({ items: [decision], agents: [], tasks: [task({ id: 5 })] });
  assert.equal(plain.review, false);
  assert.deepEqual(
    plain.actions.map((a) => a.type),
    ['answer', 'done', 'open', 'read'],
  );
  const [card] = myTurn({ items: [decision], agents: [], tasks: [task({ id: 5, tags: ['fe', 'review'] })] });
  assert.equal(card.review, true);
  assert.deepEqual(
    card.actions.map((a) => a.type),
    ['answer', 'approve', 'ask_change', 'open', 'read'],
  );
  assert.equal(card.primary, 3);
});

test('the pictures of a review come from the first comment: named in its text, else its image files', () => {
  assert.deepEqual(reviewPictures([]), []);
  const named = { id: 2, parts: [{ text: 'see' }, { image: { id: 'a', name: 'one.png' } }, { image: { id: 'b' }, gone: true }] };
  assert.deepEqual(reviewPictures([{ id: 9, parts: [{ image: { id: 'z' } }] }, named]), [{ id: 'a', name: 'one.png' }]);
  const filed = {
    id: 3,
    files: [
      { id: 'f', kind: 'image', name: 'x.png' },
      { id: 'g', kind: 'markdown' },
    ],
  };
  assert.deepEqual(reviewPictures([filed]), [{ id: 'f', name: 'x.png' }]);
  assert.equal(changeAsked(' wider '), 'Change asked: wider');
});

test('an open task given to the person is a card; one an unread item already stands for is counted once', () => {
  const tasks = [
    task({ id: 1, to: 'human' }),
    task({ id: 2, to: 'human' }),
    task({ id: 3, to: 'human', state: 'done' }),
    task({ id: 4, to: 'someone' }),
  ];
  const cards = myTurn({ items: [item({ id: 9, kind: 'decision', task: 2 })], agents: [], tasks });
  assert.deepEqual(cards.map((c) => [c.key, c.kind]).sort(), [
    ['item:9', 'decision'],
    ['task:1', 'task'],
  ]);
  assert.deepEqual(
    cards.find((c) => c.key === 'task:1')!.actions.map((a) => a.type),
    ['done_task', 'open'],
  );
});

test('the agents of My turn are the ones its cards name', () => {
  const cards = myTurn({
    items: [],
    agents: [agent({ name: 'b', needs_you: { kind: 'dialog', since: '2026-10-15T00:00:00Z', text: null } as AgentView['needs_you'] })],
    tasks: [task({ id: 5, to: 'human', from: 'c' })],
  });
  assert.deepEqual([...turnAgents(cards)], ['b']);
});

test('an ask from a task comment is answered by a reply, OK or Approve, and never closes the task', () => {
  const ask = item({ id: 9, kind: 'ask', task: 5, memo: 77, agent: 'a', body: 'Ship it?' });
  const [plain] = myTurn({ items: [ask], agents: [], tasks: [task({ id: 5 })] });
  assert.equal(plain!.memo, 77);
  assert.deepEqual(plain!.actions.slice(0, plain!.primary), [
    { type: 'reply', id: 9, text: 'OK', label: 'OK' },
    { type: 'answer', id: 9 },
  ]);
  const [review] = myTurn({ items: [ask], agents: [], tasks: [task({ id: 5, tags: ['review'] })] });
  assert.deepEqual(review!.actions.slice(0, review!.primary), [
    { type: 'reply', id: 9, text: 'Approved', label: 'Approve' },
    { type: 'change', id: 9 },
  ]);
  assert.ok(![...plain!.actions, ...review!.actions].some((a) => a.type === 'done' || a.type === 'approve' || a.type === 'ask_change'));
});

test('one message row per agent with unread messages, newest first, opening at the first above the cursor', () => {
  const rows = messageRows([
    agent({ name: 'a', founder_unread: 2, founder_seen: 10, last_to_founder: { text: 'old', at: '2026-10-15T00:00:00Z' } }),
    agent({ name: 'b', founder_unread: 1, last_to_founder: { text: 'new', at: '2026-10-16T00:00:00Z' } }),
    agent({ name: 'c', founder_unread: 0 }),
    agent({ name: 'd' }),
  ]);
  assert.deepEqual(
    rows.map((r) => [r.agent, r.line, r.unread, r.from]),
    [
      ['b', 'new', 1, null],
      ['a', 'old', 2, 11],
    ],
  );
});
