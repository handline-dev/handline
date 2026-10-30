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
  const dialog = agent({
    name: 'd',
    needs_you: { kind: 'dialog', since: '2026-10-15T00:00:00Z', title: 'Waiting at a dialog', text: 'x', source: 'hook' },
  });
  const asks = agent({
    name: 'a',
    needs_you: { kind: 'permission', since: '2026-10-15T00:00:00Z', title: 'Waiting for your OK', text: null, source: 'hook' },
  });
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

test("a daemon stall's card uses the daemon's title and sentence, not one built from its kind", () => {
  const stall = (name: string, kind: 'trust' | 'start_failed' | 'no_session', title: string, text: string) =>
    agent({ name, needs_you: { kind, since: '2026-10-15T00:00:00Z', title, text, source: 'daemon' } });
  const cards = myTurn({
    items: [],
    agents: [
      stall(
        't',
        'trust',
        'Waiting for folder trust',
        'Claude Code is asking whether to trust the files in /w. Only you can answer it, in the terminal.',
      ),
      stall('s', 'start_failed', "Won't start", 'The last 3 starts failed: x.'),
      stall('n', 'no_session', "Woke but didn't start", 'It was woken 3 times and no session started: y.'),
    ],
    tasks: [],
  });
  assert.deepEqual(cards.map((c) => [c.agent, c.title, c.detail]).sort(), [
    ['n', "Woke but didn't start", 'It was woken 3 times and no session started: y.'],
    ['s', "Won't start", 'The last 3 starts failed: x.'],
    ['t', 'Waiting for folder trust', 'Claude Code is asking whether to trust the files in /w. Only you can answer it, in the terminal.'],
  ]);
});

test('a stall the daemon raised has one button: Try again for a start that failed, the terminal otherwise', () => {
  const stall = (name: string, kind: 'trust' | 'start_failed' | 'no_session') =>
    agent({ name, needs_you: { kind, since: '2026-10-15T00:00:00Z', title: 't', text: null, source: 'daemon' } });
  const cards = myTurn({ items: [], agents: [stall('t', 'trust'), stall('s', 'start_failed'), stall('n', 'no_session')], tasks: [] });
  const actions = (name: string) => cards.find((c) => c.agent === name)!.actions.map((a) => a.type);
  assert.deepEqual(actions('t'), ['terminal']);
  assert.deepEqual(actions('s'), ['retry']);
  assert.deepEqual(actions('n'), ['retry']);
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

test('a worktree-removal decision has three answers, not Done', () => {
  const [card] = myTurn({
    items: [item({ id: 7, kind: 'decision', task: 5 })],
    agents: [],
    tasks: [task({ id: 5, tags: ['worktree-removal'] })],
  });
  assert.deepEqual(
    card.actions.map((a) => (a.type === 'worktree' ? a.answer : a.type)),
    ['answer', 'keep', 'save', 'force', 'open', 'read'],
  );
  assert.equal(card.primary, 4);
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
    agents: [
      agent({
        name: 'b',
        needs_you: { kind: 'dialog', since: '2026-10-15T00:00:00Z', title: 'Waiting at a dialog', text: null } as AgentView['needs_you'],
      }),
    ],
    tasks: [task({ id: 5, to: 'human', from: 'c' })],
  });
  assert.deepEqual([...turnAgents(cards)], ['b']);
});

test('a permission card is titled by what is asked, not the raw command line, which is its detail', () => {
  const raw = 'Bash: cd /Users/user/orca/workspaces/harbor && ./check.sh';
  const [c] = myTurn({
    items: [item({ id: 4, title: raw, prompt: { ...perm, command: 'cd /Users/user/x && ./check.sh' } as never })],
    agents: [],
    tasks: [],
  });
  assert.equal(c!.title, 'Asks to use Bash');
  assert.equal(c!.detail, 'cd /Users/user/x && ./check.sh');
  const [bare] = myTurn({ items: [item({ id: 5, title: raw, prompt: { ...perm, tool: '' } as never })], agents: [], tasks: [] });
  assert.equal(bare!.title, 'Asks for a yes');
});

test('an ask from a task comment is answered by a reply, OK or Approve, and never closes the task', () => {
  const ask = item({ id: 9, kind: 'ask', task: 5, memo: 77, agent: 'a', body: 'Ship it?' });
  const [plain] = myTurn({ items: [ask], agents: [], tasks: [task({ id: 5 })] });
  assert.equal(plain!.memo, 77);
  assert.deepEqual(plain!.actions.slice(0, plain!.primary), [
    { type: 'reply', id: 9, text: 'OK', label: 'Go ahead' },
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

test('a task with an open `@human` ask is shown once, by the ask, and its text drops the mention', () => {
  const ask = item({ id: 9, kind: 'ask', task: 5, memo: 77, body: '@human is 16 px fine?' });
  const old = item({ id: 8, kind: 'decision', task: 5 });
  const cards = myTurn({ items: [old, ask], agents: [], tasks: [task({ id: 5, to: 'human', tags: ['review'] })], me: 'human' });
  assert.deepEqual(
    cards.map((c) => c.key),
    ['item:9'],
  );
  assert.equal(cards[0]!.detail, 'is 16 px fine?');
  assert.deepEqual(
    myTurn({ items: [ask], agents: [], tasks: [task({ id: 5, to: 'human' })], me: 'human' }).map((c) => c.key),
    ['item:9'],
  );
});

test('an ask on a task outside the list is titled with the task read by its id, and keeps its comment as the text', () => {
  const ask = item({
    id: 9,
    kind: 'ask',
    task: 1684,
    memo: 77,
    title: '@human should the tab strip wrap?',
    body: '@human should the tab strip wrap or scroll?',
  }); // local-ref-ok
  const [c] = myTurn({ items: [ask], agents: [], tasks: [], titles: new Map([[1684, 'Tab strip']]), me: 'human' }); // local-ref-ok
  assert.equal(c!.title, 'Tab strip');
  assert.equal(c!.detail, 'should the tab strip wrap or scroll?');
});

test('an ask whose task is not known yet drops the mention from its title and keeps the comment as the text', () => {
  const ask = item({
    id: 9,
    kind: 'ask',
    task: 1684,
    memo: 77,
    title: '@human should the tab strip wrap?',
    body: '@human should the tab strip wrap?',
  }); // local-ref-ok
  const [c] = myTurn({ items: [ask], agents: [], tasks: [], me: 'human' });
  assert.equal(c!.title, 'should the tab strip wrap?');
  assert.equal(c!.detail, 'should the tab strip wrap?');
});

test('a mention in the middle of a sentence leaves the sentence its colon', () => {
  const ask = item({ id: 9, kind: 'ask', task: 5, memo: 77, body: 'Second look please @human: the margin is now 24 px.' });
  const [c] = myTurn({ items: [ask], agents: [], tasks: [task({ id: 5, to: 'human' })], me: 'human' });
  assert.equal(c!.detail, 'Second look please: the margin is now 24 px.');
});
