import type { AgentView, Prompt } from '@handline/contract';
import type { FakeTranscript } from './transcript.ts';
import type { FakeChat } from './chat.ts';
import type { FakeInbox } from './inbox.ts';
import type { FakeTasks } from './tasks.ts';

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
  /** AgentView fields the agent_view_fields and agent_view_attention conformance fixtures pin, served as they are. */
  view?: Partial<Pick<AgentView, 'kind' | 'task_count' | 'ready' | 'wall' | 'fire' | 'attention' | 'cache_ignored' | 'config_error'>>;
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
    parent: 'handline.architect',
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
    parent: 'handline.architect',
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
  chat.seedItem(A, { at: ago(52), kind: 'session', session: { id: 's1', started: ago(52), handoff: null } });
  chat.seedItem(A, {
    at: ago(46),
    kind: 'prompt',
    open: false,
    prompt: { id: 'p1', agent: A, kind: 'permission', tool: 'Bash', command: 'git status', lines: [], options: [], since: ago(46) },
  });
  chat.seedItem(A, { at: ago(48), kind: 'tool', tool: 'Read', text: 'docs/pc-app-gaps.md' });
  chat.seedItem(A, { at: ago(47), kind: 'task_event', task_event: { event: 'opened', task: 1684, request: null, evidence: null } });
  chat.seedItem(A, { at: ago(25), kind: 'task_event', task_event: { event: 'done', task: 1684, request: null, evidence: 'auditor PASS' } });
  chat.seedItem(A, { at: ago(20), kind: 'task_event', task_event: { event: 'changed', task: 1684, request: null, evidence: null, change: 'assigned to alice, priority 2 -> 1' } });
  chat.seedItem(A, {
    at: ago(10),
    kind: 'task_event',
    task_event: { event: 'withdrawn', task: 1684, request: null, evidence: 'duplicate of T3' },
  });
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

/** A small tree of tasks: a parent with two children, one running, one blocked, and a done one. */
export function seedTasks(tasks: FakeTasks, at: string): void {
  const base = Date.parse(at);
  const ago = (min: number) => new Date(base - min * 60_000).toISOString();
  const me = 'founder';
  tasks.seed({
    id: 1684,
    from: me,
    to: 'handline.architect',
    title: 'Web UI design',
    state: 'doing',
    priority: 1,
    created: ago(300),
    delivered: ago(240),
    time_box: '8h',
    body: 'Plan the web UI.\n\nThree units, chat first.',
  });
  tasks.seed({
    id: 1688,
    from: 'handline.architect',
    to: 'handline.term-build',
    title: 'Terminal pane on the bytes channel',
    state: 'doing',
    parent: 1684,
    created: ago(200),
    delivered: ago(180),
    time_box: '4h',
    body: 'Build the pane.',
  });
  tasks.seed({
    id: 1690,
    from: 'handline.architect',
    to: 'handline.auditor',
    title: 'Audit the terminal pane',
    state: 'queued',
    parent: 1684,
    created: ago(90),
    time_box: '1h',
    priority: 3,
    blockers: [{ task: 1688, reason: 'waits for the pane' }],
  });
  tasks.seed({
    id: 1700,
    from: me,
    to: 'handline.term-build',
    title: 'Draft: pair the web UI with the real daemon',
    state: 'draft',
    created: ago(60),
    time_box: '2h',
  });
  tasks.seed({
    id: 1650,
    from: me,
    to: 'handline.architect',
    title: 'Contract fixtures',
    state: 'done',
    created: ago(2000),
    delivered: ago(1900),
    done: ago(1500),
    evidence: 'abc1234',
    priority: 2,
  });
}

/** The auditor as a structured session: one finished turn with every kind of event. A test opens a permission with `transcript.ask`. */
export function seedTranscript(tr: FakeTranscript): void {
  const A = 'handline.auditor',
    t1 = '6f1c0a52-0000-4000-8000-000000000001';
  const prompt: Prompt = {
    id: 'toolu_a',
    agent: A,
    kind: 'permission',
    tool: 'Bash',
    command: 'python3 tests/infra/test_contract.py',
    reason: null,
    lines: [],
    options: [
      { key: 'allow', label: 'Allow', answer: 'yes', highlighted: true },
      { key: 'deny', label: 'Deny', answer: 'no' },
    ],
    since: '2026-10-08T21:00:00Z',
  };
  tr.structure(A);
  tr.push(A, { kind: 'turn_start', turn: t1, turn_start: { message: t1, from: 'founder', text: 'Run the contract tests.' } });
  tr.push(A, { kind: 'text', turn: t1, item: 41, text: 'Running them now.' });
  tr.push(A, { kind: 'tool_use', turn: t1, tool_use: { id: 'toolu_a', tool: 'Bash', input: { command: prompt.command } } });
  tr.push(A, { kind: 'permission', turn: t1, item: 42, permission: { tool_use_id: 'toolu_a', prompt } });
  tr.push(A, {
    kind: 'permission_answer',
    turn: t1,
    permission_answer: { tool_use_id: 'toolu_a', behavior: 'allow', by: 'person', rule: null, message: null },
  });
  tr.push(A, {
    kind: 'tool_result',
    turn: t1,
    tool_result: { tool_use_id: 'toolu_a', ok: true, text: '26 passed, 0 failed', truncated: false },
  });
  tr.push(A, { kind: 'error', turn: t1, error: { code: 'rate_limited', text: 'The API asked to retry; retrying.' } });
  tr.push(A, {
    kind: 'turn_end',
    turn: t1,
    turn_end: {
      reason: 'done',
      cost_usd: 0.04,
      duration_ms: 8200,
      usage: { input: 1200, output: 210, cache_read: 9000, cache_write: 300 },
    },
  });
}
