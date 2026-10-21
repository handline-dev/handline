// What the worktree pages count. Pure.
import type { Worktree } from '@handline/client';

/** The lines a worktree adds and deletes, over its changed files. */
export const worktreeSums = (w: Worktree): { added: number; deleted: number } => ({
  added: w.changed.reduce((n, f) => n + f.added, 0),
  deleted: w.changed.reduce((n, f) => n + f.deleted, 0),
});
