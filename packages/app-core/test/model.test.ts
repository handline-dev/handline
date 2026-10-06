import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentDetail, AgentView, Prompt } from '@handline/client';
import {
  controlsOf, dotOf, holderLabel, loadLayout, needsConfirm, offered, railGroups, saveLayout, keyFrom, DEFAULT_LAYOUT, DAEMON_KEYS, type KV,
} from '../src/index.ts';

const agent = (name: string, over: Partial<AgentView> = {}): AgentView => ({
  name, role: 'implementer', model: 'opus', effort: null, state: 'idle', state_since: new Date().toISOString(), account: 'alpha',
  task: null, cache_until: null, has_dialog: false, parent: null, ...over,
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

test('the rail sorts waiting agents up, nests children under their parent and filters', () => {
  const list = [agent('b'), agent('a'), agent('w', { has_dialog: true }), agent('kid', { parent: 'b' }), agent('p', { state: 'parked', role: 'auditor' })];
  const g = railGroups(list, 'role');
  assert.deepEqual(g.map((x) => x.title), ['implementer', 'auditor']);
  assert.deepEqual(g[0].rows.map((r) => [r.agent.name, r.depth]), [['w', 0], ['a', 0], ['b', 0], ['kid', 1]]);
  assert.deepEqual(railGroups(list, 'role', { hideParked: true }).map((x) => x.title), ['implementer']);
  assert.deepEqual(railGroups(list, 'role', { search: 'KID' }).flatMap((x) => x.rows.map((r) => r.agent.name)), ['kid']);
  assert.deepEqual(railGroups(list, 'account').map((x) => x.title), ['alpha']);
});

test('a child whose parent is filtered out still shows', () => {
  const list = [agent('b'), agent('kid', { parent: 'b', role: 'x' })];
  assert.deepEqual(railGroups(list, 'role', { search: 'kid' })[0].rows.map((r) => r.agent.name), ['kid']);
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
  const p = (kind: string): Prompt => ({ id: '1', agent: 'a', kind, lines: [], since: '', options: [
    { key: '1', label: 'Yes', answer: 'yes' }, { key: '2', label: 'Yes, always', answer: 'other' }, { key: '3', label: 'No', answer: 'no' }] });
  assert.deepEqual(offered(p('permission')).map((o) => o.key), ['1', '3']);
  assert.equal(offered(p('trust')).length, 3);
});

test('kill always asks; close asks only mid-turn', () => {
  assert.equal(needsConfirm('kill', agent('a')), true);
  assert.equal(needsConfirm('close', agent('a')), false);
  assert.equal(needsConfirm('close', agent('a', { state: 'running' })), true);
  assert.equal(needsConfirm('interrupt', agent('a', { state: 'running' })), false);
});

test('controls follow the session and who holds the input', () => {
  const d = (over: object) => ({ ...agent('a'), worktree: null, terminal: null, session_id: null, session_open: false, input_holders: [{ kind: 'daemon', name: 'daemon', since: null }],
    account_pick: { account: null, reason: '' }, prompt: null, ...over }) as AgentDetail;
  assert.deepEqual(Object.entries(controlsOf(d({}), 'me')).filter(([, v]) => v).map(([k]) => k), ['start']);
  assert.deepEqual(Object.entries(controlsOf(d({ session_id: 's' }), 'me')).filter(([, v]) => v).map(([k]) => k), ['resume']);
  assert.deepEqual(Object.entries(controlsOf(d({ session_id: 's', session_open: true }), 'me')).filter(([, v]) => v).map(([k]) => k), ['stop', 'take']);
  const mine = d({ session_open: true, input_holders: [{ kind: 'person', name: 'me', since: '' }] });
  assert.deepEqual(Object.entries(controlsOf(mine, 'me')).filter(([, v]) => v).map(([k]) => k), ['stop', 'release', 'typing']);
  assert.equal(controlsOf(mine, 'you').take, false); // someone else holds it
  assert.equal(holderLabel(mine.input_holders?.[0], 'me'), 'YOU');
});

test('the pane layout is remembered per agent and survives bad or blocked storage', () => {
  const mem = new Map<string, string>();
  const kv: KV = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => { mem.set(k, v); } };
  saveLayout(kv, 'a', { dock: 'bottom', open: false, tab: 'chat' });
  assert.deepEqual(loadLayout(kv, 'a'), { dock: 'bottom', open: false, tab: 'chat' });
  assert.deepEqual(loadLayout(kv, 'b'), DEFAULT_LAYOUT);
  mem.set('layout:c', '{not json'); assert.deepEqual(loadLayout(kv, 'c'), DEFAULT_LAYOUT);
  mem.set('layout:d', '{"dock":"sideways"}'); assert.deepEqual(loadLayout(kv, 'd'), DEFAULT_LAYOUT);
  const throwing: KV = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.deepEqual(loadLayout(throwing, 'a'), DEFAULT_LAYOUT);
  assert.doesNotThrow(() => saveLayout(throwing, 'a', DEFAULT_LAYOUT));
  assert.deepEqual(loadLayout(null, 'a'), DEFAULT_LAYOUT);
});
