import { describe, expect, it } from 'vitest';
import type { AgentView } from '@handline/client';
import { agentLine, doingWords } from '#src/agent-line.ts';

const agent = (over: Record<string, unknown>) => ({ name: 'a', state: 'idle', ...over }) as unknown as AgentView;

describe('doingWords', () => {
  it('says a tool call in words, never the command or the path', () => {
    expect(doingWords('Bash cd /Users/ron/orca && git fetch')).toBe('Running a command');
    expect(doingWords('Edit /Users/ron/orca/apps/web/src/AgentRow.tsx')).toBe('Editing AgentRow.tsx');
    expect(doingWords('Read /a/b/c.ts')).toBe('Reading c.ts');
    expect(doingWords('Grep foo')).toBe('Searching the code');
  });
  it('cuts the paths of a text it does not know to their last part', () => {
    expect(doingWords('Checking /Users/ron/orca/workspaces/x')).toBe('Checking x');
  });
});

describe('agentLine', () => {
  const task = { id: 5745, title: 'Trim the list' };
  it('prefers what it is doing now, then the held task, then its last message', () => {
    const last_to_founder = { text: 'Done with the review\nmore', at: 't' };
    expect(agentLine(agent({ state: 'running', doing: { text: 'Bash ls', at: 't' }, task, last_to_founder }))).toBe('Running a command');
    expect(agentLine(agent({ task, last_to_founder }))).toBe('T5745 Trim the list');
    expect(agentLine(agent({ last_to_founder }))).toBe('Done with the review');
  });
  it('shows a request the founder has not seen before the task', () => {
    expect(agentLine(agent({ task, founder_unread: 1, last_to_founder: { text: 'Pick A or B?', at: 't' } }))).toBe('Pick A or B?');
  });
  it('leaves out a system nudge, the state word and the folder', () => {
    expect(agentLine(agent({ last_to_founder: { text: 'T3867 has waited 2h on you', at: 't' }, state_detail: 'idle' }))).toBe('');
  });
});
