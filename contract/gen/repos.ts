// Generated from contract/spec/ by contract/generate.py; do not edit.
/* eslint-disable */
import type { Accepted, AgentName, Repo, RequestId, Role, Timestamp } from './core.ts';

export type ChangedFile = {
  path: string;
  /** M, A, D, R or ?. */
  status: string;
  added: number;
  deleted: number;
};

export type Worktree = {
  path: string;
  repo: string;
  branch: string;
  owner: AgentName | null;
  parent: string | null;
  children: string[];
  status: string;
  changed: ChangedFile[];
  /** Present and true only on a worktree no agent records: one an agent left behind when it was retired, renamed or moved. Its owner is null. Never the repo's own checkout or a locked worktree. */
  orphan?: boolean;
  /** Orphans only: whether its HEAD is merged into main. */
  merged?: boolean;
  /** Orphans only: its changed and untracked files, as git status counts them. */
  files_changed?: number;
  /** Orphans only: its commits not on main. */
  commits_ahead?: number;
  /** Orphans only: the bytes it takes on disk, as housekeeping last measured them (at most hourly, off the daemon's tick); null until measured. */
  size_bytes?: number | null;
};

export interface ReposMethods {
  "repos.list": { params: { [key: string]: unknown }; result: {
    repos: Repo[];
  } };
  "worktrees.list": { params: {
  repo?: string;
  owner?: AgentName;
}; result: {
    worktrees: Worktree[];
  } };
  "worktree.diff": { params: {
  path: string;
  file?: string;
}; result: {
    path: string;
    file: string | null;
    /** Unified diff. */
    diff: string;
    truncated: boolean;
  } };
  "worktree.remove": { params: {
  /** The orphan's path, as worktrees.list gives it. */
  path: string;
  /** Remove it even though it holds work: files changed, or commits not on main. The person's confirm; nothing asks again. */
  force?: boolean;
  request_id: RequestId;
}; result: Accepted & {
    /** Whether its branch went too: only for one merged into main and clean, by `git branch -d`. A forced removal leaves the branch. */
    branch_deleted: boolean;
  } };
  "file.read": { params: {
  agent: AgentName;
  /** Relative to the agent's worktree, with `/` between parts. No .., no empty or . part, not absolute, no backslash, and no part of it (nor the worktree path under it) may be a symlink: bad_request (field path) otherwise. */
  path: string;
}; result: {
    /** UTF-8 text; the first 1 MiB when the file is longer (truncated). */
    text: string;
    /** The file's size in bytes. */
    size: number;
    /** The file is over 1 MiB and text is its first 1 MiB. Do not write it back. */
    truncated: boolean;
    mtime: Timestamp;
    /** SHA-256 of the whole file, hex: file.write takes it back. */
    etag: string;
  } };
  "file.write": { params: {
  agent: AgentName;
  /** Relative to the agent's worktree, with `/` between parts. No .., no empty or . part, not absolute, no backslash, and no part of it (nor the worktree path under it) may be a symlink: bad_request (field path) otherwise. */
  path: string;
  /** The whole new content: UTF-8 text without NUL, at most 1 MiB; for a .json file, valid JSON. */
  text: string;
  /** The etag file.read gave; empty to create a file that must not exist (its folder must). */
  etag: string;
  request_id: RequestId;
}; result: {
    size: number;
    mtime: Timestamp;
    /** The new file's etag. */
    etag: string;
  } };
  "worktree.set_status": { params: {
  path: string;
  status: string;
  request_id: RequestId;
}; result: Accepted };
}

export const REPOS_METHODS: { [K in keyof ReposMethods]: { role: Role; callers: Role[] | null; write: boolean; since: number } } = {
  "repos.list": { role: "member", callers: null, write: false, since: 1 },
  "worktrees.list": { role: "member", callers: null, write: false, since: 1 },
  "worktree.diff": { role: "member", callers: null, write: false, since: 1 },
  "worktree.remove": { role: "founder", callers: null, write: true, since: 1 },
  "file.read": { role: "founder", callers: null, write: false, since: 1 },
  "file.write": { role: "founder", callers: null, write: true, since: 1 },
  "worktree.set_status": { role: "member", callers: null, write: true, since: 1 },
};
