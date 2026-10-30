// The fake daemon's repos and worktrees: read-only but for worktree.remove, as the Python and Go daemons serve them today.
import { createHash } from 'node:crypto';
import type { Repo, Worktree } from '@handline/contract';
import { Failure, invalidParams, refused } from '#src/errors.ts';
import { AGENTS } from '#src/fixtures.ts';

/** The methods this answers. */
const WORKTREES = /^(repos\.list|worktrees\.list|worktree\.diff|worktree\.remove|files\.list|file\.read|file\.write)$/;

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

/** What every worktree carries besides its changed files: base, files and commits, the size housekeeping measured, its pull request. */
const facts = (
  files: number,
  more: Partial<Pick<Worktree, 'base' | 'commits_ahead' | 'size_bytes' | 'pr'>> = {},
): Pick<Worktree, 'base' | 'files_changed' | 'commits_ahead' | 'size_bytes' | 'pr'> => ({
  base: 'main',
  files_changed: files,
  commits_ahead: 0,
  size_bytes: null,
  pr: null,
  ...more,
});

/** A worktree no agent records, with nothing in it yet. */
const plain = (path: string, branch: string): Omit<Worktree, 'base' | 'files_changed' | 'commits_ahead' | 'size_bytes' | 'pr'> => ({
  path,
  repo: 'handline',
  branch,
  owner: null,
  parent: null,
  children: [],
  status: 'todo',
  changed: [],
});

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
    ...facts(changed[a.name]?.length ?? 0),
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
      ...facts(0),
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
      ...facts(2, { base: 'master' }),
    },
  );
  return rows;
};

/** Branches the worktrees.list facts fixtures read: clean, dirty, checks failing, a ready pull request. */
export const factsWorktrees = (): Worktree[] => [
  {
    ...plain('/work/facts-clean', 'facts/clean'),
    ...facts(0),
  },
  {
    ...plain('/work/facts-dirty', 'facts/dirty'),
    changed: [file('src/a.ts', 'M', 4, 1), file('src/b.ts', '?', 0, 0)],
    ...facts(2, { commits_ahead: 2, size_bytes: 52_428_800 }),
  },
  {
    ...plain('/work/facts-failing', 'facts/failing'),
    ...facts(0, {
      commits_ahead: 3,
      pr: { number: 901, url: 'https://github.com/handline-dev/handline/pull/901', verdict: 'checks_failing', failing: 2 },
    }),
  },
  {
    ...plain('/work/facts-ready', 'facts/ready'),
    ...facts(0, { commits_ahead: 1, pr: { number: 902, url: 'https://github.com/handline-dev/handline/pull/902', verdict: 'ready' } }),
  },
];

/** The folders and file names file.write never touches, and the only endings it writes, as the contract's `denied` gate says. */
const DENIED_DIRS = ['.git', '.claude', '.github', '.husky', '.codex', '.cursor', '.vscode', '.devcontainer', 'hooks'];
const DENIED_NAMES = [
  'settings.json',
  'settings.local.json',
  '.mcp.json',
  'mcp.json',
  'hooks.json',
  'package.json',
  '.claude.json',
  'tsconfig.json',
  'turbo.json',
  'deno.json',
  'composer.json',
  'lefthook.json',
];
const etagOf = (text: string) => createHash('sha256').update(text).digest('hex');
const FILE_CAP = 1 << 20;

export class FakeWorktrees {
  /** The files file.read serves, by `agent:path`; file.write changes them. */
  files = new Map<string, { text: string; mtime: string }>([
    ['handline.architect:docs/big.md', { text: 'a'.repeat((1 << 20) + 10), mtime: '2026-10-17T09:00:00Z' }],
    ['handline.architect:docs/pc-app-gaps.md', { text: '# PC app gaps\n\n- the File tab\n', mtime: '2026-10-17T09:00:00Z' }],
  ]);

  /** What files.list sees of each agent's worktree: git's tracked and untracked files, the ignored ones already left out. */
  listed = new Map<string, { path: string; size: number }[]>([
    [
      'handline.architect',
      [
        ['.gitignore', 7],
        ['README.md', 6],
        ['docs/domain.md', 1],
        ['lib/main.go', 1],
        ['main/run.sh', 1],
        ['notes/todo.md', 1],
        ['src/app/Main.tsx', 1],
        ['src/app/main_test.go', 1],
        ['src/my_animation.ts', 1],
      ].map(([path, size]) => ({ path: path as string, size: size as number })),
    ],
  ]);

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
      case 'worktree.remove': {
        if (typeof p.path !== 'string' || !p.path) throw invalidParams('worktree.remove needs a path', 'path');
        const w = this.rows.find((r) => r.path === p.path);
        if (!w) throw new Failure('not_found', `no worktree ${p.path}`, { what: 'path' });
        if (!w.orphan) throw refused('orphan', `${p.path} is an agent's worktree: it goes only with the agent.`);
        const held = (w.files_changed ?? 0) > 0 || (w.commits_ahead ?? 0) > 0;
        if (held && p.force !== true)
          throw refused(
            'dirty',
            `${p.path} holds work: ${w.files_changed ?? 0} files changed, ${w.commits_ahead ?? 0} commits not on main.`,
            undefined,
            {
              files_changed: w.files_changed ?? 0,
              commits_ahead: w.commits_ahead ?? 0,
            },
          );
        this.rows = this.rows.filter((r) => r !== w);
        return { accepted: true, said: `${p.path} is removed.`, branch_deleted: !held && w.merged === true };
      }
      case 'files.list':
        return this.list(p);
      case 'file.read':
      case 'file.write':
        return this.file(method, p);
      default:
        throw new Failure('refused', `the fake daemon does not serve ${method}`, { reason: 'unsupported', gate: 'unsupported' });
    }
  }

  /** files.list as the contract ranks it: inside the name, then elsewhere in the path, then spread out; shorter, then byte order. */
  private list(p: Record<string, unknown>): unknown {
    const { agent, query, limit } = p;
    if (typeof agent !== 'string' || !agent) throw invalidParams('agent is an agent name', 'agent');
    if (query !== undefined && query !== null && typeof query !== 'string') throw invalidParams('query is a string', 'query');
    if (limit !== undefined && limit !== null && (!Number.isInteger(limit) || (limit as number) < 1 || (limit as number) > 200))
      throw invalidParams('limit is an integer from 1 to 200', 'limit');
    const files = this.listed.get(agent);
    if (!files) throw new Failure('not_found', `no worktree for ${agent}`, { what: 'agent' });
    const q = typeof query === 'string' ? query.toLowerCase() : '';
    const tier = (path: string): number => {
      const lower = path.toLowerCase();
      if (lower.slice(lower.lastIndexOf('/') + 1).includes(q)) return 0;
      if (lower.includes(q)) return 1;
      let i = 0;
      for (const c of lower) if (i < q.length && c === q[i]) i++;
      return i === q.length ? 2 : -1;
    };
    const bytes = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
    const hits = files
      .map((f) => ({ f, t: q ? tier(f.path) : 0 }))
      .filter((h) => h.t >= 0)
      .sort((a, b) => (q ? a.t - b.t || a.f.path.length - b.f.path.length : 0) || bytes(a.f.path, b.f.path));
    const n = (limit as number | undefined) ?? 50;
    return { files: hits.slice(0, n).map((h) => ({ ...h.f })), more: hits.length > n };
  }

  private file(method: string, p: Record<string, unknown>): unknown {
    const { agent, path } = p;
    if (typeof agent !== 'string' || !agent) throw invalidParams('agent is an agent name', 'agent');
    if (
      typeof path !== 'string' ||
      !path ||
      path.startsWith('/') ||
      /[\\\0]/.test(path) ||
      path.split('/').some((x) => x === '' || x === '.' || x === '..')
    )
      throw invalidParams('path is relative to the agent worktree', 'path');
    const key = `${agent}:${path}`;
    const have = this.files.get(key);
    if (method === 'file.read') {
      if (!have) throw new Failure('not_found', `${path} is not there`, { what: 'file' });
      const cut = have.text.length > FILE_CAP;
      return { text: have.text.slice(0, FILE_CAP), size: have.text.length, truncated: cut, mtime: have.mtime, etag: etagOf(have.text) };
    }
    const { text, etag } = p;
    if (typeof text !== 'string' || text.length > FILE_CAP) throw invalidParams('text is the whole new content', 'text');
    if (typeof etag !== 'string') throw invalidParams('etag is the etag read, or empty for a new file', 'etag');
    const parts = path.toLowerCase().split('/');
    const name = parts.pop()!;
    if (parts.some((d) => DENIED_DIRS.includes(d)) || DENIED_NAMES.includes(name) || !/\.(md|json)$/.test(name))
      throw refused('denied', `${path} is not a file the daemon writes`);
    if (name.endsWith('.json')) {
      try {
        JSON.parse(text);
      } catch {
        throw invalidParams('text is not valid JSON', 'text');
      }
    }
    if (have && have.text.length > FILE_CAP) throw refused('too_big', `${path} is over 1 MiB: it is not written back`);
    if (have ? etagOf(have.text) !== etag : etag !== '') throw refused('stale', `${path} changed since that etag: read it again`);
    const mtime = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
    this.files.set(key, { text, mtime });
    return { size: text.length, mtime, etag: etagOf(text) };
  }
}
