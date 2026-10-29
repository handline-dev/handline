// The task rows a fresh fake daemon starts with.
import type { TaskStore } from '#src/taskstore.ts';

/** A small tree of tasks: a parent with two children, one running, one blocked, and a done one. */
export function seedTasks(tasks: TaskStore, at: string): void {
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
    segments: [
      { kind: 'queued', on: null, text: 'queued', task: null, from: ago(300), to: ago(240), seconds: 3600 },
      { kind: 'working', on: null, text: '', task: null, from: ago(240), to: ago(180), seconds: 3600 },
      { kind: 'waiting', on: 'founder', text: 'needs you', task: null, from: ago(180), to: ago(150), seconds: 1800 },
      { kind: 'working', on: null, text: '', task: null, from: ago(150), to: ago(135), seconds: 900 },
      { kind: 'waiting', on: 'task', text: 'blocked by T1688', task: 1688, from: ago(135), to: ago(125), seconds: 600 },
      { kind: 'working', on: null, text: '', task: null, from: ago(125), to: ago(105), seconds: 1200 },
      { kind: 'waiting', on: 'agent', text: 'waiting on handline.auditor', task: null, from: ago(105), to: ago(95), seconds: 600 },
      { kind: 'working', on: null, text: '', task: null, from: ago(95), to: ago(65), seconds: 1800 },
      { kind: 'waiting', on: 'account', text: 'wall', task: null, from: ago(65), to: ago(55), seconds: 600 },
      { kind: 'working', on: null, text: '', task: null, from: ago(55), to: at, seconds: 3300 },
    ],
    body: 'Plan the web UI.\n\nThree units, chat first.',
    history: [
      { at: ago(300), by: me, field: 'state', was: '', now: 'queued' },
      { at: ago(300), by: me, field: 'assignee', was: '', now: 'handline.architect' },
      { at: ago(290), by: 'handline.architect', field: 'state', was: 'queued', now: 'doing' },
      { at: ago(280), by: me, field: 'title', was: 'Web UI plan', now: 'Web UI design' },
      {
        at: ago(270),
        by: 'handline.architect',
        field: 'body',
        was: 'Plan the web UI.',
        now: 'Plan the web UI.\n\nThree units, chat first.',
      },
      { at: ago(262), by: 'handline.pc', field: 'memo', was: '', now: 'Chat first, then tasks.', memo: 1 },
      {
        at: ago(250),
        by: 'handline.auditor',
        field: 'state',
        was: 'doing',
        now: 'done',
        note: 'auditor PASS on 3f2a9c1; screenshot attached',
      },
      {
        at: ago(246),
        by: 'handline.pc',
        field: 'memo',
        was: 'Chat first, then tasks.',
        now: 'Chat first, then tasks, then the timeline.',
        memo: 1,
      },
      { at: ago(244), by: me, field: 'memo', was: 'Skip the gap list for now.', now: '', memo: 2 },
      { at: ago(240), by: me, field: 'assignee', was: 'handline.term-build', now: 'handline.architect' },
      { at: ago(236), by: me, field: 'priority', was: 'P2', now: 'P1' },
    ],
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
