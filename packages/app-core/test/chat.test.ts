import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { setup } from '@handline/fake-daemon/helpers';
import {
  Store,
  noticeOf,
  takeBack,
  landingLabel,
  tickLevel,
  sessionLine,
  handoffLine,
  sideOf,
  taskRow,
  changeParts,
  timeline,
  chatEntries,
  activity,
  restartCause,
  restartingLine,
  restartShown,
  teamActivityOf,
  turnSummary,
  turnMinutes,
} from '#src/index.ts';

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
  delivered_at: null,
  typed_at: null,
  landed_at: null,
  parent: null,
  kind: 'message',
  tasks: [],
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

test('the message that tells an agent how an HR proposal was decided is left out of its chat, the card shows it', () => {
  const outcome = msg({ id: 4, body: 'proposal 858cde9b applied: x', proposal: 7 });
  const ids = (scope: 'mine' | 'all') => timeline([outcome, msg({ id: 5, body: 'ok' })], [], scope).map((e) => e.id);
  assert.deepEqual(ids('mine'), [5]);
  assert.deepEqual(ids('all'), [4, 5]);
});

test("a reply with the same words as a message is still shown: the agent answering 'ok' to 'ok'", () => {
  const out = timeline([msg({ id: 3, body: 'ok' })], [item({ id: 8, text: 'ok' })]);
  assert.deepEqual(
    out.map((e) => `${e.kind}${e.id}`),
    ['message3', 'item8'],
  );
});

test('who a message is from', () => {
  const mine = msg({ landing: 'typed', from: 'Handline desktop' });
  const ask = msg({ from: A, to: 'founder', state: 'queued', landing: 'delivered' });
  assert.equal(sideOf(mine, A, 'Handline desktop'), 'person');
  assert.equal(sideOf(ask, A, 'Handline desktop'), 'agent');
  assert.equal(sideOf(msg({ from: 'handline.auditor' }), A, 'Handline desktop'), 'other');
  for (const old of ['delivered_unanswered', 'answered', 'dismissed']) assert.equal(landingLabel(old as never), 'Delivered'); // a daemon still sends these
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
    task_event: { event, task: 7, request: null, evidence: null, who: 'human', ...extra },
  }) as TranscriptItem;
const titles = new Map([[7, 'Fix the tray']]);

test('a task line says added, edited, done or deleted', () => {
  assert.equal(taskRow(taskItem('opened'), titles).label, 'Task added');
  assert.equal(taskRow(taskItem('changed'), titles).label, 'Task changed');
  assert.equal(taskRow(taskItem('done'), titles).label, 'Task done');
  assert.equal(taskRow(taskItem('withdrawn'), titles).label, 'Task deleted');
});

test('a task line carries the task, its title and only the change or evidence', () => {
  assert.deepEqual(taskRow(taskItem('changed', { change: 'priority 2 -> 1' }), titles), {
    label: 'Task changed',
    who: 'human',
    task: 7,
    title: 'Fix the tray',
    detail: 'priority 2 -> 1',
    parts: [{ field: 'priority', old: '2', now: '1' }],
  });
  assert.deepEqual(changeParts('assigned to a.b, queued -> doing, title changed'), [
    { text: 'assigned to a.b' },
    { field: '', old: 'queued', now: 'doing' },
    { text: 'title changed' },
  ]);
  assert.equal(taskRow(taskItem('withdrawn', { evidence: 'duplicate' })).detail, 'duplicate');
  assert.equal(taskRow(taskItem('opened'), titles).detail, null);
  assert.equal(taskRow(taskItem('opened')).title, null);
});

const pm = (o: Partial<ChatMessage>) =>
  ({ id: 5, from: 'Handline desktop', to: 'handline.x', landing: 'queued', state: 'queued', ...o }) as ChatMessage;

test("only the person's own words are on their side: the CLI, this device, to whichever agent", () => {
  assert.equal(sideOf(pm({}), A, 'Handline desktop'), 'person');
  assert.equal(sideOf(pm({ from: 'human' }), A, 'Handline desktop'), 'person');
  assert.equal(sideOf(pm({ from: 'handline.y' }), A, 'Handline desktop'), 'other');
  assert.equal(sideOf(pm({ from: 'task-board', to: A }), A, 'Handline desktop'), 'other');
  assert.equal(sideOf(pm({ from: 'schedule', to: A }), A, ''), 'other');
});

test('the ticks: none until sent, one sent and not read, two read', () => {
  assert.equal(tickLevel(pm({ id: -1 })), 0);
  assert.equal(tickLevel(pm({})), 0);
  assert.equal(tickLevel(pm({ landing: 'typed' })), 1);
  assert.equal(tickLevel(pm({ landing: 'delivered' })), 2);
  assert.equal(tickLevel(pm({ landing: 'delivered', receipt: { state: 'sent' } as ChatMessage['receipt'] })), 1);
  assert.equal(tickLevel(pm({ landing: 'delivered', receipt: { state: 'confirmed' } as ChatMessage['receipt'] })), 2);
});

test('a message can be taken back until it is withdrawn, and the agent is asked first once it has it', () => {
  assert.deepEqual(takeBack(pm({ landing: 'queued' })), { confirm: false });
  assert.deepEqual(takeBack(pm({ landing: 'typed' })), { confirm: false });
  assert.deepEqual(takeBack(pm({ landing: 'delivered' })), { confirm: true });
  assert.deepEqual(takeBack(pm({ landing: 'delivered_unanswered' as never })), { confirm: true }); // a daemon still sends it
  assert.equal(takeBack(pm({ landing: 'withdrawn' })), null);
  const r = (state: 'queued' | 'sent' | 'confirmed') => pm({ landing: 'typed', receipt: { state, uuid: 'u' } as never });
  assert.deepEqual(takeBack(r('queued')), { confirm: false });
  assert.deepEqual(takeBack(r('sent')), { confirm: true });
});

test("the task board's words are left out of the agent's own chat and shown in the All view", () => {
  const news = pm({ id: 7, from: 'task-board', to: A, body: 'T3492 done by handline.pair: -' }); // local-ref-ok
  const delivery = pm({ id: 8, from: 'task-board', to: A, body: '#28387 to handline.pair was written to its session' }); // local-ref-ok
  assert.deepEqual(noticeOf(news), { kind: 'task', task: 3492 });
  assert.deepEqual(noticeOf(delivery), { kind: 'delivery' });
  assert.equal(noticeOf(pm({ from: 'human', body: 'T1 done by me' })), null);
  const kinds = (scope: 'mine' | 'all') => timeline([news, delivery], [], scope).map((e) => `${e.kind}${e.id}`);
  const waits = pm({ id: 9, from: 'task-board', to: A, body: 'task T4136 now waits on T4148' }); // local-ref-ok
  assert.deepEqual(
    timeline([news, delivery, waits, pm({ id: 10, from: 'human', to: A })], [], 'mine').map((e) => `${e.kind}${e.id}`),
    ['message10'],
  );
  assert.deepEqual(kinds('mine'), []);
  assert.deepEqual(kinds('all'), ['note7', 'message8']);
  assert.equal(sideOf(delivery, A, 'Handline desktop'), 'other');
});

test('a session line says why in plain words, and keeps an unknown cause as it came', () => {
  const at = '2026-01-01T00:00:00Z';
  const line = (cause?: string, handoff: string | null = null) =>
    sessionLine({ id: 1, at, kind: 'session', session: { id: 's', started: at, handoff, ...(cause ? { cause } : {}) } });
  assert.deepEqual(line('handoff', 'x'), { reason: 'Started again from a handoff', handoff: 'x' });
  assert.equal(line('restart').reason, 'Restarted');
  assert.equal(line('fresh close').reason, 'Restarted with a fresh conversation');
  assert.equal(line('wake').reason, 'Woke from a close');
  assert.equal(line('move').reason, 'Moved to another account');
  assert.equal(line('settings_applied').reason, 'Restarted to apply settings');
  assert.equal(line('crash_recovery').reason, 'Resumed after a crash');
  assert.notEqual(line('context_full').reason, line('handoff').reason);
  assert.equal(line('idle_close').reason, 'Woke after closing when idle');
  assert.equal(line('crash').reason, 'New session (crash)');
  assert.deepEqual(line(), { reason: 'New session', handoff: null });
});

test('a task card is in the chat of the agent that made the change; the all tab shows every one', () => {
  const card = (id: number, who: string) =>
    ({
      id,
      at: `2026-01-01T00:00:0${id}Z`,
      kind: 'task_event',
      task_event: { event: 'changed', task: 7, request: null, evidence: null, who },
    }) as unknown as TranscriptItem;
  const items = [card(1, 'alice'), card(2, 'bob'), card(3, 'human')];
  assert.deepEqual(
    timeline([], items, 'mine', 'alice').map((e) => e.id),
    [1, 3],
  ); // the founder acts on the agent's behalf
  assert.deepEqual(
    timeline([], items, 'all', 'alice').map((e) => e.id),
    [1, 2, 3],
  );
  assert.equal(taskRow(card(2, 'bob')).who, 'bob');
});

test('handoffLine says why and what for each phase', () => {
  assert.deepEqual(handoffLine({ cause: 'context_full', phase: 'writing' }), { why: 'Context full', what: 'Writing the handoff' });
  assert.equal(handoffLine({ cause: 'context_full', phase: 'starting' }).what, 'Starting a new session');
  assert.equal(handoffLine({ cause: 'odd', phase: 'writing' }).why, 'odd');
});

test('the Chat holds the person and the agent; Activity holds the rest, and only session marks and task cards are in both', () => {
  const talk = msg({ id: 1 });
  const peers = msg({ id: 2, from: 'handline.auditor', to: A, body: 'Review is done' });
  const news = msg({ id: 3, from: 'task-board', to: A, body: 'T9 done by x: ok' });
  const items = [item({ id: 10, kind: 'reply' }), item({ id: 11, kind: 'tool', tool: 'Bash' }), item({ id: 12, kind: 'session' })];
  const key = (es: { kind: string; id: number }[]) => es.map((e) => `${e.kind}${e.id}`);
  const task = item({ id: 13, kind: 'task_event' });
  assert.deepEqual(key(chatEntries([talk], [...items, task], A)), ['message1', 'item10', 'item12', 'item13']);
  assert.deepEqual(key(activity([talk, peers, news], [talk], [...items, task], A)), ['message2', 'note3', 'item11', 'item12', 'item13']);
});

test('activityOf lists what the agent does with the team: tools, other agents messages, not the founder chat', () => {
  const rows = teamActivityOf(
    [
      msg({ id: 1, from: 'human', to: A }),
      msg({ id: 2, from: 'handline.auditor', to: A, body: 'Review is done', at: '2026-01-01T00:00:02Z' }),
    ],
    [
      item({ id: 5, kind: 'reply', text: 'ok' }),
      item({ id: 6, kind: 'tool', tool: 'Bash', text: 'ls', output: 'a', dropped_lines: 2, at: '2026-01-01T00:00:01Z' }),
    ],
    'human',
  );
  assert.deepEqual(
    rows.map((r) => [r.kind, r.text]),
    [
      ['tools', 'Bash ls'],
      ['messages', 'Review is done'],
    ],
  );
  assert.deepEqual([rows[0]!.output, rows[0]!.dropped], ['a', 2]);
  assert.deepEqual(rows[1]!.route, { from: 'handline.auditor', to: A });
});

test('in the all room, what the agent says to another agent is not shown; what it says to the person, and what reaches it, is', () => {
  const ms = [
    msg({ id: 1, from: A, to: 'handline.auditor', body: 'say to auditor' }),
    msg({ id: 2, from: 'handline.auditor', to: A, body: 'say to me' }),
    msg({ id: 3, from: A, to: 'human', body: 'to the person' }),
    msg({ id: 4, from: A, to: 'Handline desktop', body: 'to the device' }),
  ];
  assert.deepEqual(
    timeline(ms, [], 'all', A, 'Handline desktop').map((e) => e.id),
    [2, 3, 4],
  );
  assert.deepEqual(
    timeline(ms, [], 'mine', A, 'Handline desktop').map((e) => e.id),
    [1, 2, 3, 4],
  ); // mine is left as it was
  assert.deepEqual(
    timeline(ms, [], 'all', A).map((e) => e.id),
    [2, 3],
  ); // no `me`: a device name is not known as the person
});

test('a restart is one line: back-to-back session starts collapse and keep the cause and the handoff', () => {
  const at = (n: number) => `2026-01-01T00:00:0${n}Z`;
  const start = (id: number, cause?: string, handoff: string | null = null) =>
    ({
      id,
      at: at(id),
      kind: 'session',
      session: { id: `s${id}`, started: at(id), handoff, ...(cause ? { cause } : {}) },
    }) as unknown as TranscriptItem;
  const said = pm({ id: 9, from: A, to: 'human', body: 'hi' });
  const one = timeline([], [start(1, 'context_full', 'note'), start(2, 'launch'), start(3, 'launch')]);
  assert.equal(one.length, 1);
  const item = (one[0] as { item: TranscriptItem }).item;
  assert.equal(item.id, 3);
  assert.deepEqual([item.session?.cause, item.session?.handoff], ['context_full', 'note']);
  // a message between two starts keeps them apart
  assert.equal(timeline([{ ...said, at: at(2) }], [start(1, 'launch'), start(3, 'launch')]).length, 3);
});

test('restartCause is the daemon word in short words, null when it sent none', () => {
  const at = '2026-01-01T00:00:00Z';
  const item = (cause?: string) =>
    ({
      id: 1,
      at,
      kind: 'session',
      session: { id: 's', started: at, handoff: null, ...(cause ? { cause } : {}) },
    }) as unknown as TranscriptItem;
  assert.equal(restartCause(item('context_full')), 'context was full');
  assert.equal(restartCause(item('odd')), 'odd');
  assert.equal(restartCause(item()), null);
});

test('the restart line ends where the New session line begins, never both', () => {
  const since = '2026-10-18T20:55:00.000Z';
  const session = (at: string) =>
    ({ id: 1, at, kind: 'session', session: { id: 's', started: at, handoff: null, cause: 'context_full' } }) as unknown as TranscriptItem;
  assert.equal(restartShown({ since }, []), true);
  assert.equal(restartShown({ since }, [session('2026-10-18T20:40:00.000Z')]), true); // an earlier session's line
  assert.equal(restartShown({ since }, [session('2026-10-18T20:55:45.000Z')]), false);
});

test('the live restart line reads as one sentence', () => {
  assert.equal(restartingLine({ cause: 'context_full', phase: 'writing' }), 'Restarting: context full. Writing the handoff…');
  assert.equal(restartingLine({ cause: 'context_full', phase: 'starting' }), 'Restarting: context full. Starting a new session…');
});

test('a permission request is a tool step: Activity lists it under tools, Chat keeps only other dialogs', () => {
  const perm = item({
    id: 20,
    kind: 'prompt',
    open: false,
    prompt: { kind: 'permission', tool: 'Bash', command: 'git status' } as TranscriptItem['prompt'],
  });
  const dialog = item({ id: 21, kind: 'prompt', open: true, prompt: { kind: 'choice' } as TranscriptItem['prompt'] });
  const key = (es: { kind: string; id: number }[]) => es.map((e) => `${e.kind}${e.id}`);
  assert.deepEqual(key(chatEntries([], [perm, dialog], A)), ['item21']);
  assert.deepEqual(key(activity([], [], [perm, dialog], A)), ['item20']);
  assert.deepEqual(
    teamActivityOf([], [perm], 'human').map((r) => [r.kind, r.text]),
    [['tools', 'Asked permission: Bash git status']],
  );
});

test('a turn is the tool steps after the last reply or message', () => {
  const at = (s: number) => `2026-01-01T00:00:${String(s).padStart(2, '0')}Z`;
  const items = [
    item({ id: 1, at: at(1), kind: 'tool', tool: 'Bash', text: 'old' }),
    item({ id: 2, at: at(5), kind: 'reply' }),
    item({ id: 3, at: at(6), kind: 'tool', tool: 'Bash', text: 'git status' }),
    item({ id: 4, at: at(7), kind: 'prompt', prompt: { kind: 'permission', tool: 'Edit' } as never }),
    item({ id: 5, at: at(8), kind: 'tool', tool: 'Read', text: 'a.ts' }),
  ];
  const s = turnSummary([msg({ at: at(3) })], items);
  assert.equal(s.count, 2);
  assert.equal(s.steps.length, 3);
  assert.equal(s.last, 'Read a.ts');
  assert.equal(turnSummary([msg({ at: at(9) })], items).count, 0);
  assert.equal(turnSummary([], []).last, null);
});

test('turn minutes round down and say under a minute', () => {
  const now = Date.parse('2026-01-01T00:12:30Z');
  assert.equal(turnMinutes('2026-01-01T00:00:00Z', now), '12m');
  assert.equal(turnMinutes('2026-01-01T00:12:00Z', now), '<1m');
  assert.equal(turnMinutes(null, now), '<1m');
});
