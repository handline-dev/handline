import { describe, expect, it } from 'vitest';
import type { AgentView } from '@handline/client';
import { liveOf } from '#src/AgentRow.tsx';

const view = (o: Partial<AgentView>) =>
  ({ name: 'a', state: 'running', state_since: new Date().toISOString(), state_detail: '', handoff: null, ...o }) as AgentView;

describe('liveOf', () => {
  it('shows the newest tool line of a running turn instead of the state word', () => {
    expect(liveOf(view({ doing: { text: 'Bash sleep 30', at: new Date().toISOString() } }))).toBe('Bash sleep 30');
  });

  it('falls back to the state word and age without doing, or with none yet', () => {
    expect(liveOf(view({}))).toMatch(/^working/);
    expect(liveOf(view({ doing: null }))).toMatch(/^working/);
  });

  it('ignores doing once the turn is not running', () => {
    expect(liveOf(view({ state: 'idle', doing: { text: 'Bash sleep 30', at: new Date().toISOString() } }))).toBe('');
  });
});
