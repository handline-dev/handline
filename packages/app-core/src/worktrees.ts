// What the worktree pages count. Pure.
import type { Worktree } from '@handline/client';

/** The lines a worktree adds and deletes, over its changed files. */
export const worktreeSums = (w: Worktree): { added: number; deleted: number } => ({
  added: w.changed.reduce((n, f) => n + f.added, 0),
  deleted: w.changed.reduce((n, f) => n + f.deleted, 0),
});

/** The tag of the decision the daemon opens for a worktree that holds work and has no one to ask; its answers are WORKTREE_ANSWERS. */
export const WORKTREE_REMOVAL_TAG = 'worktree-removal';
export type WorktreeAnswer = 'keep' | 'save' | 'force';

/** A worktree that holds work, as the daemon's dirty refusal lists it. */
export type HeldWorktree = { path: string; files_changed: number; commits_ahead: number };

const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What would be lost, in plain words: "3 unsaved files, 2 unpushed commits". */
export function heldWords(held: readonly HeldWorktree[]): string {
  const files = held.reduce((n, w) => n + w.files_changed, 0);
  const commits = held.reduce((n, w) => n + w.commits_ahead, 0);
  return [files ? count(files, 'unsaved file', 'unsaved files') : '', commits ? count(commits, 'unpushed commit', 'unpushed commits') : '']
    .filter(Boolean)
    .join(', ');
}

/** The worktrees a dirty refusal of agent.retire names, or none when the data is not that shape. */
export const heldFromData = (data: Record<string, unknown> | undefined): HeldWorktree[] =>
  Array.isArray(data?.worktrees)
    ? (data.worktrees as unknown[]).flatMap((w) => {
        const o = w as Partial<HeldWorktree> | null;
        return o && typeof o.path === 'string'
          ? [{ path: o.path, files_changed: Number(o.files_changed) || 0, commits_ahead: Number(o.commits_ahead) || 0 }]
          : [];
      })
    : [];
