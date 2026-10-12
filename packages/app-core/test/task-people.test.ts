import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assignGroups, involved, replyTargets, treeDistance } from '../src/index.ts';

const team = {
  agents: [
    { name: 'boss', parent: null },
    { name: 'pc', parent: 'boss' },
    { name: 'mobile', parent: 'boss' },
    { name: 'rev', parent: 'boss' },
    { name: 'pc-sub', parent: 'pc' },
    { name: 'pc-sub2', parent: 'pc-sub' },
    { name: 'pc-sub3', parent: 'pc-sub2' },
    { name: 'far', parent: null },
  ],
};
const task = {
  from: 'boss',
  to: 'pc',
  events: [
    { at: '2026-10-01T10:00:00Z', agent: 'pc', actor: null },
    { at: '2026-10-03T10:00:00Z', agent: 'rev', actor: { kind: 'agent', name: 'task-board' } },
    { at: '2026-10-02T10:00:00Z', agent: 'mobile', actor: { kind: 'user', name: 'human' } },
  ],
};
const names = ['boss', 'pc', 'mobile', 'rev', 'pc-sub', 'pc-sub2', 'pc-sub3', 'far', 'me'];

test('distance counts steps along the reports-to tree', () => {
  assert.equal(treeDistance(team, 'pc', 'pc'), 0);
  assert.equal(treeDistance(team, 'pc', 'boss'), 1);
  assert.equal(treeDistance(team, 'pc', 'mobile'), 2);
  assert.equal(treeDistance(team, 'pc-sub3', 'mobile'), 5);
  assert.equal(treeDistance(team, 'pc', 'far'), Infinity);
});

test('the picker lists who acted most recent first, then the near, then the rest by name', () => {
  const g = assignGroups(task, team, names, 'me');
  assert.deepEqual(
    g.map((x) => x.key),
    ['acted', 'near', 'rest'],
  );
  assert.deepEqual(g[0]!.names, ['rev', 'mobile', 'boss']);
  assert.deepEqual(g[1]!.names, ['pc-sub', 'pc-sub2']);
  assert.deepEqual(g[2]!.names, ['far', 'pc-sub3']);
});

test('the holder, the viewer and the system never appear, and an empty group is left out', () => {
  const all = assignGroups(task, team, [...names, 'task-board'], 'me').flatMap((g) => g.names);
  for (const n of ['pc', 'me', 'task-board']) assert.ok(!all.includes(n));
  assert.deepEqual(
    assignGroups({ ...task, events: [] }, { agents: [] }, ['a'], 'me').map((g) => g.key),
    ['rest'],
  );
});

test('reply chips are the people the task names, latest first, without the viewer', () => {
  assert.deepEqual(involved(task), ['rev', 'mobile', 'human', 'pc', 'boss']);
  assert.deepEqual(replyTargets(task, 'boss'), ['rev', 'mobile', 'pc']);
});
