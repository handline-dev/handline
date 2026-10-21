import { expect, it } from 'vitest';
import type { AgentView } from '@handline/client';
import { byTeam } from '#src/Sidebar.tsx';

const row = (name: string, depth = 0) => ({ agent: { name } as AgentView, depth });
const names = (rows: { agent: { name: string } }[]) => rows.map((r) => r.agent.name);

it('grouped, a team stands together with its children and keeps its order; ungrouped, nothing moves', () => {
  const all = ['a.one', 'b.one', 'a.two', 'b.two'];
  const rows = [row('a.one'), row('b.one'), row('a.two'), row('a.two-kid', 1), row('b.two')];
  expect(names(byTeam(rows, true, all))).toEqual(['a.one', 'a.two', 'a.two-kid', 'b.one', 'b.two']);
  expect(byTeam(rows, false, all)).toBe(rows);
});
