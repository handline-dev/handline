import type { FakeChat } from './chat.ts';
import type { FakeInbox } from './inbox.ts';

export interface AgentFixture {
  name: string;
  role: string;
  model: string;
  effort: string;
  state: string;
  account: string | null;
  task: { id: number; title: string } | null;
  parent: string | null;
  worktree: string;
  branch: string;
  session: string | null;
  wall?: boolean;
}

export const AGENTS: AgentFixture[] = [
  {
    name: 'handline.term-build',
    role: 'implementer',
    model: 'sonnet',
    effort: 'high',
    state: 'parked',
    account: null,
    task: null,
    parent: null,
    worktree: '/work/handline.term-build',
    branch: 'buddyron/handline.term-build',
    session: null,
  },
  {
    name: 'handline.architect',
    role: 'architect',
    model: 'opus',
    effort: 'high',
    state: 'idle',
    account: 'alpha',
    task: { id: 1684, title: 'Web UI design' },
    parent: null,
    worktree: '/work/handline.architect',
    branch: 'buddyron/handline.architect',
    session: 's-arch-1',
  },
  {
    name: 'handline.auditor',
    role: 'auditor',
    model: 'opus',
    effort: 'high',
    state: 'idle',
    account: 'beta',
    task: null,
    parent: null,
    worktree: '/work/handline.auditor',
    branch: 'buddyron/handline.auditor',
    session: 's-aud-1',
    wall: true,
  },
];

/** What the scripted fake session shows; the permission prompt belongs to the first turn. */
export const BANNER = ['Claude Code v2.1 (fake)', 'cwd: {worktree}', ''];
export const PROMPT_LINES = ['Bash command', '  git push origin {branch}', 'Do you want to proceed?', '  1. Yes', '  2. No'];

/** A little history for the architect, one of each message state and transcript kind, so a UI has something to draw on first load. */
export function seedChat(chat: FakeChat, at: string): void {
  const A = 'handline.architect',
    me = 'founder';
  const ago = (min: number) => new Date(Date.parse(at) - min * 60_000).toISOString();
  chat.seed(A, { from: me, to: A, body: 'Draft the web chat tab plan.', at: ago(50), landing: 'answered' });
  chat.seed(A, { from: me, to: A, body: 'Use the contract fixtures.', at: ago(30), landing: 'delivered_unanswered' });
  chat.seed(A, { from: me, to: A, body: 'Hold that, scope changed.', at: ago(20), landing: 'withdrawn' });
  chat.seed(A, { from: me, to: A, body: 'Also add the list of gaps.', at: ago(5), landing: 'typed', landed_at: null, delivered_at: null });
  chat.seed(A, {
    from: A,
    to: me,
    body: 'Which gap list should come first?',
    at: ago(10),
    kind: 'question',
    is_task_question: true,
    landing: 'delivered_unanswered',
  });
  chat.seed(A, { from: 'handline.auditor', to: A, body: 'Review is done, see the notes.', at: ago(40), landing: 'delivered' });
  chat.seedItem(A, { at: ago(49), kind: 'reply', text: 'Here is the plan: three units, chat first.' });
  chat.seedItem(A, { at: ago(48), kind: 'tool', tool: 'Read', text: 'docs/pc-app-gaps.md' });
  chat.seedItem(A, { at: ago(47), kind: 'task_event', task_event: { event: 'opened', task: 7, request: null, evidence: null } });
  chat.seedItem(A, { at: ago(25), kind: 'task_event', task_event: { event: 'done', task: 7, request: null, evidence: 'auditor PASS' } });
}

/** What waits on the founder at first: one of each kind, two of them read, so a UI draws every card and both marks. */
export function seedInbox(inbox: FakeInbox, at: string): void {
  const ago = (min: number) => new Date(Date.parse(at) - min * 60_000).toISOString();
  inbox.add({
    kind: 'result',
    agent: 'handline.term-build',
    title: 'Audit of the lint doc: PASS',
    body: 'The lint doc matches the config.',
    evidence: 'pnpm lint: 0 errors\npnpm test: 212 passed\ngit diff --stat: 4 files changed\nnothing else touched',
    task: 7,
    created: ago(60),
    read: true,
  });
  inbox.add({
    kind: 'block',
    agent: 'handline.architect',
    title: 'Blocked: the daemon does not serve inbox.reply',
    body: 'The reply box cannot send until the daemon team lands it.',
    created: ago(120),
    read: true,
  });
  inbox.add({
    kind: 'decision',
    agent: 'handline.architect',
    title: 'Should the daemon relay through the Go front door?',
    body: 'Both work; relaying means one socket for every client.',
    options: [
      { id: 'yes', label: 'Yes' },
      { id: 'no', label: 'No' },
    ],
    created: ago(12),
  });
  inbox.add({
    kind: 'prompt',
    agent: 'handline.term-build',
    title: 'Permission: Bash',
    prompt: {
      id: 'p1',
      agent: 'handline.term-build',
      kind: 'permission',
      tool: 'Bash',
      command: 'rm -rf build',
      reason: null,
      lines: [],
      options: [],
      since: ago(3),
    },
    created: ago(3),
  });
  inbox.add({
    kind: 'result',
    agent: 'handline.architect',
    title: 'Plan for the Tasks page is ready',
    body: 'Three steps; the first needs task.board.',
    evidence: null,
    task: 12,
    created: ago(1),
  });
}
