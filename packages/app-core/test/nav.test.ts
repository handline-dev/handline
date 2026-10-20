import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentView } from '@handline/client';
import { chipChanges, chipCounts, chipOf, DEFAULT_NAV, movePin, navDock, navList, navOf, togglePin, withOpen } from '#src/index.ts';

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

test('the dock is the pins, then this device’s open agents that are not pinned', () => {
  const d = navDock(team, ['orca'], ['handline.pc', 'orca', 'gone']);
  assert.deepEqual(
    [d.pinned, d.open].map((l) => l.map((a) => a.name)),
    [['orca'], ['handline.pc']],
  );
});

test('pins toggle and move; opening past the limit drops the oldest', () => {
  assert.deepEqual(togglePin(['a'], 'b'), ['a', 'b']);
  assert.deepEqual(togglePin(['a', 'b'], 'a'), ['b']);
  assert.deepEqual(movePin(['a', 'b', 'c'], 'c', 0), ['c', 'a', 'b']);
  assert.deepEqual(movePin(['a', 'b'], 'x', 0), ['a', 'b']);
  assert.deepEqual(withOpen(['a', 'b'], 'a'), ['a', 'b']);
  assert.deepEqual(withOpen(['a', 'b', 'c'], 'd', 3), ['b', 'c', 'd']);
});
