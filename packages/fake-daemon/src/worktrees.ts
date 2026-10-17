// The fake daemon's repos and worktrees: read-only, as the Python and Go daemons serve them today.
import type { Repo, Worktree } from '@handline/contract';
import { Failure, invalidParams } from './errors.ts';
import { AGENTS } from './fixtures.ts';

/** The methods this answers. */
const WORKTREES = /^(repos\.list|worktrees\.list|worktree\.diff)$/;

const REPOS: Repo[] = [
  { name: 'handline', path: '/work/handline', default_branch: 'main' },
  { name: 'docs-site', path: '/work/docs-site', default_branch: 'master' },
];

/** A unified diff for one changed file, made up from its counts: the fake has no repository to run git in. */
const unified = (c: Worktree['changed'][number]): string => {
  const add = Array.from({ length: Math.min(c.added, 6) }, (_, i) => `+added line ${i + 1}`);
  const del = Array.from({ length: Math.min(c.deleted, 6) }, (_, i) => `-removed line ${i + 1}`);
  const head = [
    `diff --git a/${c.path} b/${c.path}`,
    ...(c.status === 'A' ? ['new file mode 100644'] : c.status === 'D' ? ['deleted file mode 100644'] : []),
  ];
  return [
    ...head,
    `--- ${c.status === 'A' ? '/dev/null' : `a/${c.path}`}`,
    `+++ ${c.status === 'D' ? '/dev/null' : `b/${c.path}`}`,
    `@@ -1,${c.deleted} +1,${c.added} @@`,
    ' context line',
    ...del,
    ...add,
    '',
  ].join('\n');
};

const file = (path: string, status: string, added: number, deleted: number) => ({ path, status, added, deleted });

/** One worktree per agent fixture, plus a spare with no owner and a docs one with a status outside the four a board knows. */
const seed = (): Worktree[] => {
  const status: Record<string, string> = {
    'handline.term-build': 'in progress',
    'handline.architect': 'in review',
    'handline.auditor': 'todo',
  };
  const changed: Record<string, Worktree['changed']> = {
    'handline.term-build': [
      file('apps/web/src/Worktrees.tsx', 'A', 120, 0),
      file('apps/web/src/route.ts', 'M', 6, 1),
      file('docs/old.md', 'D', 0, 30),
    ],
    'handline.architect': [file('docs/pc-app-gaps.md', 'M', 40, 12)],
    'handline.auditor': [],
  };
  const rows: Worktree[] = AGENTS.map((a) => ({
    path: a.worktree,
    repo: 'handline',
    branch: a.branch,
    owner: a.name,
    parent: a.parent ? (AGENTS.find((p) => p.name === a.parent)?.worktree ?? null) : null,
    children: AGENTS.filter((c) => c.parent === a.name).map((c) => c.worktree),
    status: status[a.name] ?? 'todo',
    changed: changed[a.name] ?? [],
  }));
  rows.push(
    {
      path: '/work/spare',
      repo: 'handline',
      branch: 'spare/old-idea',
      owner: null,
      parent: null,
      children: [],
      status: 'done',
      changed: [],
    },
    {
      path: '/work/docs-site.writer',
      repo: 'docs-site',
      branch: 'writer/guide',
      owner: null,
      parent: null,
      children: [],
      status: 'blocked',
      changed: [file('guide/start.md', 'M', 3, 3), file('guide/new.md', '?', 0, 0)],
    },
  );
  return rows;
};

export class FakeWorktrees {
  private on: boolean;
  /** What worktrees.list serves; a test replaces it. */
  rows: Worktree[] = seed();
  repos: Repo[] = REPOS.map((r) => ({ ...r }));

  /** `on` false is a daemon that lists none of these methods. */
  constructor(on: boolean) {
    this.on = on;
  }

  lists = (name: string): boolean => this.on || !WORKTREES.test(name);

  owns = (name: string): boolean => WORKTREES.test(name);

  call(method: string, p: Record<string, unknown>): unknown {
    switch (method) {
      case 'repos.list':
        return { repos: this.repos.map((r) => ({ ...r })) };
      case 'worktrees.list': {
        if (p.repo !== undefined && typeof p.repo !== 'string') throw invalidParams('repo must be a string', 'repo');
        if (p.owner !== undefined && typeof p.owner !== 'string') throw invalidParams('owner must be a string', 'owner');
        const repo = p.repo as string | undefined;
        const rows = this.rows.filter((w) => (!repo || w.repo === repo) && (p.owner === undefined || w.owner === p.owner));
        return { worktrees: structuredClone(rows) };
      }
      case 'worktree.diff': {
        if (typeof p.path !== 'string' || !p.path) throw invalidParams('worktree.diff needs a path', 'path');
        if (p.file !== undefined && (typeof p.file !== 'string' || p.file.startsWith('/') || p.file.split('/').includes('..')))
          throw invalidParams('the file must stay inside the worktree', 'file');
        if (!this.rows.some((w) => w.path === p.path)) throw new Failure('not_found', `no worktree ${p.path}`, { what: 'path' });
        const row = this.rows.find((w) => w.path === p.path)!;
        const files = row.changed.filter((c) => (p.file === undefined ? c.status !== '?' : c.path === p.file));
        return { path: p.path, file: p.file ?? null, diff: files.map(unified).join(''), truncated: false };
      }
      default:
        throw new Failure('refused', `the fake daemon does not serve ${method}`, { reason: 'unsupported', gate: 'unsupported' });
    }
  }
}
