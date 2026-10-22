import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentView, Worktree } from '@handline/client';
import {
  chipChanges,
  chipCounts,
  chipOf,
  DEFAULT_NAV,
  movePin,
  moveInOrder,
  navDock,
  navList,
  navOf,
  togglePin,
  withOpen,
} from '#src/index.ts';

const agent = (name: string, state: string, parent: string | null, unread = false) =>
  ({ name, state, parent, role: '', founder_unread: unread ? 1 : 0 }) as unknown as AgentView;
const team = [
  agent('handline', 'idle', null),
  agent('handline.pc', 'running', 'handline'),
  agent('handline.daemon', 'waiting', 'handline', true),
  agent('handline.daemon.auditor', 'parked', 'handline.daemon'),
  agent('orca', 'idle', null, true),
];
const names = (l: { agent: AgentView; depth: number }[]) => l.map((r) => `${'-'.repeat(r.depth)}${r.agent.name}`);

test('missing prefs read as the defaults', () => {
  assert.deepEqual(navOf(undefined), DEFAULT_NAV);
  assert.deepEqual(navOf({ nav: { ...DEFAULT_NAV, layout: 'flat' } }).layout, 'flat');
});

test('the list keeps the pinned agents on top whatever the filter, and folds the tree', () => {
  const nav = { ...DEFAULT_NAV, pins: ['orca', 'gone', 'handline.pc'], ...chipChanges('needs'), collapsed: [] };
  const l = navList(team, nav);
  assert.deepEqual(
    l.pinned.map((a) => a.name),
    ['orca', 'handline.pc'],
  );
  assert.deepEqual(names(l.rows), ['handline', '-handline.daemon']);
  const folded = navList(team, { ...DEFAULT_NAV, collapsed: ['handline'] });
  assert.deepEqual(names(folded.rows), ['handline', 'orca']);
});

test('flat lists every shown agent at the top level by urgency', () => {
  const l = navList(team, { ...DEFAULT_NAV, layout: 'flat', collapsed: ['handline'] });
  assert.deepEqual(names(l.rows), ['handline.daemon', 'handline.pc', 'handline', 'orca', 'handline.daemon.auditor']);
});

test('the unread chip shows only agents with something unread', () => {
  const l = navList(team, { ...DEFAULT_NAV, ...chipChanges('unread'), layout: 'flat' });
  assert.deepEqual(names(l.rows), ['handline.daemon', 'orca']);
});

test('chips read back from the filter, and count what each would show', () => {
  assert.equal(chipOf(DEFAULT_NAV), 'all');
  assert.equal(chipOf({ ...DEFAULT_NAV, ...chipChanges('unread') }), 'unread');
  assert.equal(chipOf({ ...DEFAULT_NAV, ...chipChanges('needs') }), 'needs');
  assert.equal(chipOf({ ...DEFAULT_NAV, hidden: ['idle'] }), null);
  assert.deepEqual(chipCounts(team), { all: 5, unread: 2, needs: 1 });
});

test('the dock is the pins, then the open tabs that are not pinned, each with the branch of the worktree it shows', () => {
  const trees = [
    { path: '/w/pc', owner: 'handline.pc', branch: 'pc-main' },
    { path: '/w/spare', owner: null, branch: 'spare' },
    { path: '/w/orca', owner: 'orca', branch: 'orca-main' },
  ] as Worktree[];
  const d = navDock(
    team,
    ['orca'],
    [{ agent: 'handline.pc', path: '/w/spare' }, { agent: 'orca' }, { agent: 'gone' }, { agent: 'handline' }],
    trees,
  );
  assert.deepEqual(
    [d.pinned, d.open].map((l) => l.map((i) => `${i.agent.name}@${i.branch}`)),
    [['orca@orca-main'], ['handline.pc@spare', 'handline@null']],
  );
});

test('pins toggle and move; opening past the limit drops the oldest', () => {
  assert.deepEqual(togglePin(['a'], 'b'), ['a', 'b']);
  assert.deepEqual(togglePin(['a', 'b'], 'a'), ['b']);
  assert.deepEqual(movePin(['a', 'b', 'c'], 'c', 0), ['c', 'a', 'b']);
  assert.deepEqual(movePin(['a', 'b'], 'x', 0), ['a', 'b']);
  const tabs = (...names: string[]) => names.map((agent) => ({ agent }));
  assert.deepEqual(withOpen(tabs('a', 'b'), { agent: 'a', path: '/w' }), [{ agent: 'a', path: '/w' }, { agent: 'b' }]);
  assert.deepEqual(withOpen(tabs('a', 'b', 'c'), { agent: 'd' }, [], 3), tabs('b', 'c', 'd'));
  assert.deepEqual(withOpen(tabs('p', 'b', 'c'), { agent: 'd' }, ['p'], 2), tabs('p', 'c', 'd'));
});

test('the tree follows the saved order under one parent and at the top, with unlisted agents after, by name', () => {
  const nav = { ...DEFAULT_NAV, order: ['orca', 'handline.pc', 'handline.daemon'] };
  assert.deepEqual(names(navList(team, nav).rows), ['orca', 'handline', '-handline.pc', '-handline.daemon', '--handline.daemon.auditor']);
  const none = navList(team, { ...DEFAULT_NAV, order: [] });
  assert.deepEqual(names(none.rows), ['handline', '-handline.daemon', '--handline.daemon.auditor', '-handline.pc', 'orca']);
});

test('a filter keeps the order of the agents it shows, and the flat layout ignores it', () => {
  const nav = { ...DEFAULT_NAV, order: ['orca', 'handline'] };
  assert.deepEqual(names(navList(team, { ...nav, ...chipChanges('unread') }).rows), ['orca', 'handline', '-handline.daemon']);
  assert.deepEqual(names(navList(team, { ...nav, layout: 'flat' }).rows)[0], 'handline.daemon');
});

test('moving an agent among its siblings rewrites only their part of the order', () => {
  assert.deepEqual(moveInOrder(['x', 'a', 'b', 'c'], ['a', 'b', 'c'], 'c', 0), ['x', 'c', 'a', 'b']);
  assert.deepEqual(moveInOrder([], ['a', 'b'], 'a', 9), ['b', 'a']);
  assert.deepEqual(moveInOrder(['a'], ['a', 'b'], 'gone', 0), ['a']);
});
