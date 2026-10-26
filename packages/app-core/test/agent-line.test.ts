import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import type { AgentView, Status } from '@handline/client';
import { agentLine, doingWords, rowWord } from '#src/agent-line.ts';

const agent = (over: Record<string, unknown>) => ({ name: 'a', state: 'idle', ...over }) as unknown as AgentView;

describe('doingWords', () => {
  it('says a tool call in words, never the command or the path', () => {
    assert.equal(doingWords('Bash cd /Users/ron/orca && git fetch'), 'Running a command');
    assert.equal(doingWords('Edit /Users/ron/orca/apps/web/src/AgentRow.tsx'), 'Editing AgentRow.tsx');
    assert.equal(doingWords('Read /a/b/c.ts'), 'Reading c.ts');
    assert.equal(doingWords('Read /Users/ron/.claude/projects/-Users-ron-workspaces-orcapipe-handline'), 'Reading a file'); // Claude's project folder
    assert.equal(doingWords('Read -Users-ron-workspaces-orcapipe-handline'), 'Reading a file');
    assert.equal(doingWords('Grep foo'), 'Searching the code');
  });
  it('cuts the paths of a text it does not know to their last part', () => {
    assert.equal(doingWords('Checking /Users/ron/orca/workspaces/x'), 'Checking x');
  });
});

describe('agentLine', () => {
  it('says a load hold in a few words, the numbers staying in the chat', () => {
    const status: Status = {
      state: 'waiting',
      reason: 'load',
      text: 'held: machine load - load 2.19 per core, limit 1.50',
      until: null,
      since: '2026-10-26T12:00:00Z',
      tasks: [],
    };
    assert.equal(agentLine(agent({ status })), 'waiting: machine load');
  });

  const task = { id: 5745, title: 'Trim the list' };
  it('prefers what it is doing now, then the held task, then its last message', () => {
    const last_to_founder = { text: 'Done with the review\nmore', at: 't' };
    assert.equal(agentLine(agent({ state: 'running', doing: { text: 'Bash ls', at: 't' }, task, last_to_founder })), 'Running a command');
    assert.equal(agentLine(agent({ task, last_to_founder })), 'T5745 Trim the list');
    assert.equal(agentLine(agent({ last_to_founder })), 'Done with the review');
  });
  it('shows a request the founder has not seen before the task', () => {
    assert.equal(agentLine(agent({ task, founder_unread: 1, last_to_founder: { text: 'Pick A or B?', at: 't' } })), 'Pick A or B?');
  });
  it('leaves out a system nudge, the state word and the folder', () => {
    assert.equal(agentLine(agent({ last_to_founder: { text: 'T3867 has waited 2h on you', at: 't' }, state_detail: 'idle' })), '');
  });
});

describe('the daemon words for a quiet state', () => {
  const quiet = (state: string, text: string) => agent({ status: { state, reason: 'none', text, since: 1 } });
  it('draws no line and no word for idle or no terminal; the icon says it', () => {
    assert.equal(agentLine(quiet('idle', 'idle')), '');
    assert.equal(agentLine(quiet('off', 'no terminal')), '');
    assert.equal(rowWord(quiet('idle', 'idle')), '');
    assert.equal(rowWord(quiet('off', 'no terminal')), '');
  });
  it('keeps what means something', () => {
    const waits = agent({ status: { state: 'waiting', reason: 'clock', text: 'wakes at 10:00', since: 1 } });
    assert.equal(agentLine(waits), 'wakes at 10:00');
    assert.equal(rowWord(waits), 'waiting');
  });
});
