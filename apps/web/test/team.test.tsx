// The Team board's pure parts: who is grouped under whom, and the state glyph an agent's whole view gets.
import { describe, expect, it } from 'vitest';
import type { AgentView, TeamAgent } from '@handline/client';
import { groups } from '../src/TeamPage.tsx';
import { stateOfView } from '../src/ui/index.ts';

const member = (name: string, parent: string | null) => ({ name, parent }) as TeamAgent;
const view = (over: Partial<AgentView>) => ({ state: 'idle', block: null, has_dialog: false, ...over }) as AgentView;

describe('groups', () => {
  it('puts the roots first and each manager’s reports after, each sorted by name', () => {
    const out = groups([
      member('c', 'boss'),
      member('b', 'boss'),
      member('boss', null),
      member('z', null),
      member('x', 'ann'),
      member('ann', null),
    ]);
    expect(out.map((g) => [g.parent, g.agents.map((a) => a.name)])).toEqual([
      [null, ['ann', 'boss', 'z']],
      ['ann', ['x']],
      ['boss', ['b', 'c']],
    ]);
  });
});

describe('stateOfView', () => {
  it('reads the block, the dialog and the missing terminal, not the state word alone', () => {
    expect(stateOfView(view({ block: { kind: 'wall', since: 'x' } }))).toBe('blocked'); // idle, but blocked
    expect(stateOfView(view({ has_dialog: true }))).toBe('needs');
    expect(stateOfView(view({ state: 'no_terminal' }))).toBe('noTerminal');
    expect(stateOfView(view({ state: 'running' }))).toBe('working');
    expect(stateOfView(view({ state: 'parked' }))).toBe('parked');
  });
});
