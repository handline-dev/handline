import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentDetail, AgentView, Prompt } from '@handline/client';
import {
  promptChoices,
  promptTitle,
  controlsOf,
  dotOf,
  holderLabel,
  loadLayout,
  needsConfirm,
  offered,
  railGroups,
  visibleRows,
  queueOf,
  RAIL_FILTERS,
  RAIL_STATE_WORD,
  stopModesOf,
  shortName,
  teamOf,
  type Dot,
  saveLayout,
  keyFrom,
  DEFAULT_LAYOUT,
  DAEMON_KEYS,
  type KV,
} from '../src/index.ts';

const agent = (name: string, over: Partial<AgentView> = {}): AgentView =>
  ({
    name,
    role: 'implementer',
    model: 'opus',
    effort: null,
    state: 'idle',
    state_since: new Date().toISOString(),
    account: 'alpha',
    task: null,
    cache_until: null,
    has_dialog: false,
    parent: null,
    ...over,
  }) as AgentView;
const key = (k: string, o: object = {}) => ({ key: k, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, ...o });

test('the dot follows the daemon state, with a dialog or block taking over', () => {
  assert.equal(dotOf(agent('a', { state: 'running' })), 'running');
  assert.equal(dotOf(agent('a', { state: 'running', has_dialog: true })), 'waiting');
  assert.equal(dotOf(agent('a', { block: { kind: 'wall', since: '' } })), 'blocked');
  assert.equal(dotOf(agent('a', { state: 'no_terminal' })), 'none');
  assert.equal(dotOf(agent('a', { state: 'parked' })), 'parked');
  assert.equal(dotOf(agent('a', { state: 'something_new' })), 'idle');
});

test('the rail groups by team, sorts waiting up, nests children, and filters by dot', () => {
  const list = [
    agent('t.b'),
    agent('t.a'),
    agent('t.w', { has_dialog: true }),
    agent('t.kid', { parent: 't.b' }),
    agent('u.p', { state: 'parked' }),
    agent('u.q', { state: 'queued' }),
    agent('solo'),
  ];
  const g = railGroups(list, 'team');
  assert.deepEqual(
    g.map((x) => x.title),
    ['t', 'u', 'agents'],
  );
  assert.deepEqual(
    g[0].rows.map((r) => [r.agent.name, r.depth]),
    [
      ['t.w', 0],
      ['t.a', 0],
      ['t.b', 0],
      ['t.kid', 1],
    ],
  );
  assert.deepEqual(
    railGroups(list, 'team', { hidden: new Set<Dot>(['parked', 'waking']) }).map((x) => x.title),
    ['t', 'agents'],
  );
  assert.deepEqual(
    railGroups(list, 'team', { search: 'KID' }).flatMap((x) => x.rows.map((r) => r.agent.name)),
    ['t.kid'],
  );
  assert.equal(teamOf('t', ['t', 't.a']), 't');
  assert.equal(shortName('t.a', 't'), 'a');
  assert.equal(shortName('t', 't'), 't');
  assert.equal(dotOf(agent('x', { state: 'queued' })), 'waking');
});

test('a child whose parent is filtered out still shows', () => {
  const list = [agent('b'), agent('kid', { parent: 'b', role: 'x' })];
  assert.deepEqual(
    railGroups(list, 'team', { search: 'kid' })[0].rows.map((r) => r.agent.name),
    ['kid'],
  );
});

test('the rail without groups is one list that nests by parent, and a collapsed parent hides its descendants', () => {
  const list = [agent('a'), agent('a.k', { parent: 'a' }), agent('a.k.g', { parent: 'a.k' }), agent('b'), agent('b.k', { parent: 'b' })];
  const [g] = railGroups(list, 'none');
  assert.equal(g.title, '');
  assert.deepEqual(
    g.rows.map((r) => [r.agent.name, r.depth, r.kids]),
    [
      ['a', 0, 1],
      ['a.k', 1, 1],
      ['a.k.g', 2, 0],
      ['b', 0, 1],
      ['b.k', 1, 0],
    ],
  );
  const names = (c: string[]) => visibleRows(g.rows, new Set(c)).map((r) => r.agent.name);
  assert.deepEqual(names([]), ['a', 'a.k', 'a.k.g', 'b', 'b.k']);
  assert.deepEqual(names(['a']), ['a', 'b', 'b.k']);
  assert.deepEqual(names(['a.k']), ['a', 'a.k', 'b', 'b.k']);
  assert.deepEqual(names(['a', 'b']), ['a', 'b']);
});

test('every rail dot has a state word, and the filter choices cover each dot once', () => {
  const dots = Object.keys(RAIL_STATE_WORD) as Dot[];
  const covered = RAIL_FILTERS.flatMap((f) => f.dots).sort();
  assert.deepEqual(covered, [...dots].sort());
  assert.equal(RAIL_STATE_WORD[dotOf(agent('x', { state: 'queued' }))], 'waking');
  assert.equal(RAIL_STATE_WORD[dotOf(agent('x', { state: 'no_terminal' }))], 'sleeping');
});

test('queue counts are null when the view does not carry them, never a made-up 0', () => {
  assert.deepEqual(queueOf(agent('x')), { tasks: null, messages: null });
  assert.deepEqual(queueOf(agent('x', { task_count: 0, unreached: 3 })), { tasks: 0, messages: 3 });
});

test('keydown maps to named keys only; printable text and IME composition are left to the textarea', () => {
  assert.deepEqual(keyFrom(key('Escape'), false), { name: 'esc' });
  assert.deepEqual(keyFrom(key('Tab', { shiftKey: true }), false), { name: 'shift-tab' });
  assert.deepEqual(keyFrom(key('c', { ctrlKey: true }), false), { name: 'ctrl-c' });
  assert.equal(keyFrom(key('a'), false), null); // arrives as an input event
  assert.equal(keyFrom(key('Process'), false), null); // IME
  assert.equal(keyFrom(key('ㅎ', { isComposing: true }), false), null);
  assert.equal(keyFrom(key('Enter', { isComposing: true }), false), null); // Enter that commits a composition
  assert.equal(keyFrom(key('v', { metaKey: true }), false), null); // paste
  assert.equal(keyFrom(key('Shift'), false), null);
  assert.deepEqual(keyFrom(key('2'), true), { name: '2' });
  assert.deepEqual(keyFrom(key('a'), true), { swallow: true }); // only keys pass while a dialog is open
});

test('a key the daemon cannot take yet is reported, and goes through once the names are known', () => {
  assert.deepEqual(keyFrom(key('Backspace'), false), { unavailable: 'Backspace' });
  assert.deepEqual(keyFrom(key('ArrowLeft'), false), { unavailable: 'ArrowLeft' });
  const later = new Set([...DAEMON_KEYS, 'backspace', 'left']);
  assert.deepEqual(keyFrom(key('Backspace'), false, later), { name: 'backspace' });
  assert.deepEqual(keyFrom(key('ArrowLeft'), false, later), { name: 'left' });
});

test('a permission card offers only yes and no; other dialogs offer everything', () => {
  const p = (kind: string): Prompt => ({
    id: '1',
    agent: 'a',
    kind,
    lines: [],
    since: '',
    options: [
      { key: '1', label: 'Yes', answer: 'yes' },
      { key: '2', label: 'Yes, always', answer: 'other' },
      { key: '3', label: 'No', answer: 'no' },
    ],
  });
  assert.deepEqual(
    offered(p('permission')).map((o) => o.key),
    ['1', '3'],
  );
  assert.equal(offered(p('trust')).length, 3);
});

test('kill always asks; close asks only mid-turn', () => {
  assert.equal(needsConfirm('kill', agent('a')), true);
  assert.equal(needsConfirm('close', agent('a')), false);
  assert.equal(needsConfirm('close', agent('a', { state: 'running' })), true);
  assert.equal(needsConfirm('interrupt', agent('a', { state: 'running' })), false);
});

test('controls follow the session and who holds the input', () => {
  const d = (over: object) =>
    ({
      ...agent('a'),
      worktree: null,
      terminal: null,
      session_id: null,
      session_open: false,
      input_holders: [{ kind: 'daemon', name: 'daemon', since: null }],
      account_pick: { account: null, reason: '' },
      prompt: null,
      ...over,
    }) as AgentDetail;
  assert.deepEqual(
    Object.entries(controlsOf(d({}), 'me'))
      .filter(([, v]) => v)
      .map(([k]) => k),
    ['start'],
  );
  assert.deepEqual(
    Object.entries(controlsOf(d({ session_id: 's' }), 'me'))
      .filter(([, v]) => v)
      .map(([k]) => k),
    ['resume'],
  );
  assert.deepEqual(
    Object.entries(controlsOf(d({ session_id: 's', session_open: true }), 'me'))
      .filter(([, v]) => v)
      .map(([k]) => k),
    ['stop', 'take'],
  );
  const mine = d({ session_open: true, input_holders: [{ kind: 'person', name: 'me', since: '' }] });
  assert.deepEqual(
    Object.entries(controlsOf(mine, 'me'))
      .filter(([, v]) => v)
      .map(([k]) => k),
    ['stop', 'release', 'typing'],
  );
  assert.equal(controlsOf(mine, 'you').take, false); // someone else holds it
  assert.equal(holderLabel(mine.input_holders?.[0], 'me'), 'YOU');
});

test('the pane layout is remembered per agent and survives bad or blocked storage', () => {
  const mem = new Map<string, string>();
  const kv: KV = {
    getItem: (k) => mem.get(k) ?? null,
    setItem: (k, v) => {
      mem.set(k, v);
    },
  };
  saveLayout(kv, 'a', { dock: 'bottom', open: false, tab: 'chat' });
  assert.deepEqual(loadLayout(kv, 'a'), { dock: 'bottom', open: false, tab: 'chat' });
  assert.deepEqual(loadLayout(kv, 'b'), DEFAULT_LAYOUT);
  mem.set('layout:c', '{not json');
  assert.deepEqual(loadLayout(kv, 'c'), DEFAULT_LAYOUT);
  mem.set('layout:d', '{"dock":"sideways"}');
  assert.deepEqual(loadLayout(kv, 'd'), DEFAULT_LAYOUT);
  const throwing: KV = {
    getItem() {
      throw new Error('blocked');
    },
    setItem() {
      throw new Error('blocked');
    },
  };
  assert.deepEqual(loadLayout(throwing, 'a'), DEFAULT_LAYOUT);
  assert.doesNotThrow(() => saveLayout(throwing, 'a', DEFAULT_LAYOUT));
  assert.deepEqual(loadLayout(null, 'a'), DEFAULT_LAYOUT);
});

const opt = (key: string, label: string, answer: 'yes' | 'no' | 'other' = 'other') => ({ key, label, answer, highlighted: false });
const card = (kind: string) =>
  ({
    id: 'p',
    agent: 'a',
    kind,
    tool: 'Bash',
    command: null,
    reason: null,
    lines: [],
    since: null,
    options: [opt('1', 'Yes', 'yes'), opt('2', 'Yes, always', 'yes'), opt('3', 'No', 'no')],
  }) as unknown as Prompt;

test('a prompt card is titled by its kind', () => {
  assert.equal(promptTitle(card('permission')), 'Permission: Bash');
  assert.equal(promptTitle(card('question')), 'Question');
  assert.equal(promptTitle(card('dialog')), 'Dialog');
  assert.equal(promptTitle(card('trust')), 'Dialog: trust');
});

test('a card offers a permission yes and no only, a dialog its always options only when a rule can be kept, and a question everything', () => {
  const keys = (p: Prompt, rules: boolean) => promptChoices(p, rules).map((o) => o.key);
  assert.deepEqual(keys(card('permission'), true), ['1', '3']);
  assert.deepEqual(keys(card('dialog'), true), ['1', '2', '3']);
  assert.deepEqual(keys(card('dialog'), false), ['1', '3']);
  assert.deepEqual(keys(card('question'), false), ['1', '2', '3']);
});

test('a structured agent is never held, taken or typed to; it is interrupted only while a turn runs or waits', () => {
  const d = (over: object) =>
    ({
      ...agent('a'),
      structured: true,
      terminal: null,
      session_id: null,
      session_open: false,
      input_holders: [],
      ...over,
    }) as unknown as AgentDetail;
  const on = (x: AgentDetail) =>
    Object.entries(controlsOf(x, 'me'))
      .filter(([, v]) => v)
      .map(([k]) => k);
  assert.deepEqual(on(d({ session_open: true, session_id: 's', state: 'running' })), ['stop', 'interrupt']);
  assert.deepEqual(on(d({ session_open: true, session_id: 's', state: 'waiting' })), ['stop', 'interrupt']);
  assert.deepEqual(on(d({ session_open: true, session_id: 's', state: 'idle' })), ['stop']);
  assert.deepEqual(on(d({ session_id: 's', state: 'parked' })), ['resume']);
  assert.deepEqual(on(d({ state: 'stalled', session_open: true })), ['stop']);
  assert.deepEqual(on(d({})), ['start']);
});

test('the stop modes of a structured agent are in session words and drop Exit', () => {
  assert.deepEqual(
    stopModesOf({ structured: true }).map((m) => m.mode),
    ['interrupt', 'close', 'kill'],
  );
  assert.deepEqual(
    stopModesOf({}).map((m) => m.mode),
    ['interrupt', 'exit', 'close', 'kill'],
  );
  assert.ok(stopModesOf({ structured: true }).every((m) => !/terminal|Esc|exit/i.test(m.hint)));
});
