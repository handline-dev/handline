import type { AgentState } from '../src/protocol.ts';

export interface AgentFixture {
  name: string; role: string; model: string; effort: string; state: AgentState;
  account: string | null; task: string | null; parent: string | null;
  worktree: string; branch: string; session: string | null; wall?: boolean;
}

export const AGENTS: AgentFixture[] = [
  { name: 'handline.term-build', role: 'implementer', model: 'sonnet', effort: 'high', state: 'parked',
    account: null, task: null, parent: null, worktree: '/work/handline.term-build', branch: 'buddyron/handline.term-build', session: null },
  { name: 'handline.architect', role: 'architect', model: 'opus', effort: 'high', state: 'idle',
    account: 'alpha', task: 'T1684', parent: null, worktree: '/work/handline.architect', branch: 'buddyron/handline.architect', session: 's-arch-1' },
  { name: 'handline.auditor', role: 'auditor', model: 'opus', effort: 'high', state: 'idle',
    account: 'beta', task: null, parent: null, worktree: '/work/handline.auditor', branch: 'buddyron/handline.auditor', session: 's-aud-1', wall: true },
];

/** What the scripted fake session shows; the permission prompt belongs to the first turn. */
export const BANNER = ['Claude Code v2.1 (fake)', 'cwd: {worktree}', ''];
export const PROMPT_LINES = [
  'Bash command',
  '  git push origin {branch}',
  'Do you want to proceed?',
  '  1. Yes',
  '  2. No',
];
