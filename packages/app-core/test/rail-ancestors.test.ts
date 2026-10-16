import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentView } from '@handline/client';
import { railRows } from '../src/model.ts';

const agent = (name: string, state: string, parent: string | null) => ({ name, state, parent, role: '' }) as unknown as AgentView;
const team = [
  agent('handline', 'idle', null),
  agent('handline.pc', 'parked', 'handline'),
  agent('handline.pc.worker', 'idle', 'handline.pc'),
  agent('handline.pc.auditor', 'running', 'handline.pc'),
  agent('handline.pr', 'idle', 'handline'),
];
const tree = (rows: ReturnType<typeof railRows>) => rows.map((r) => `${'-'.repeat(r.depth)}${r.agent.name}`);

test('a filter that hides a parent leaves it in the tree while a child of it shows', () => {
  assert.deepEqual(tree(railRows(team, { hidden: new Set(['parked']) })), [
    'handline',
    '-handline.pr',
    '-handline.pc',
    '--handline.pc.auditor',
    '--handline.pc.worker',
  ]);
});

test('a parent goes once nothing under it shows', () => {
  assert.deepEqual(tree(railRows(team, { hidden: new Set(['parked', 'idle', 'running']) })), []);
});

test('a search lists what matches, parents or not', () => {
  assert.deepEqual(tree(railRows(team, { search: 'auditor' })), ['handline.pc.auditor']);
});

test('a parent the filter hides stays at every level above a child that shows', () => {
  const deep = [agent('a', 'parked', null), agent('a.b', 'parked', 'a'), agent('a.b.c', 'idle', 'a.b')];
  assert.deepEqual(tree(railRows(deep, { hidden: new Set(['parked']) })), ['a', '-a.b', '--a.b.c']);
});
