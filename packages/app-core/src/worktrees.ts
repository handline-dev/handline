// What the worktree pages count. Pure.
import type { Worktree } from '@handline/client';
import type { WorktreeAnswer } from '#src/my-turn.ts';

/** The lines a worktree adds and deletes, over its changed files. */
export const worktreeSums = (w: Worktree): { added: number; deleted: number } => ({
  added: w.changed.reduce((n, f) => n + f.added, 0),
  deleted: w.changed.reduce((n, f) => n + f.deleted, 0),
});

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

/** A worktree-removal body ends with the member and how to close the task by number: the facts are what comes before, the buttons do the rest. */
export const worktreeFacts = (body: string): { facts: string; member: string } => ({
  facts: body.split(/\n+Member: /)[0]?.trim() ?? body,
  member: /^Member: (.+)$/m.exec(body)?.[1]?.trim() ?? 'the member',
});

/** The line after a worktree-removal answer the daemon accepted. */
export const worktreeSaid = (a: WorktreeAnswer, member: string): string =>
  ({
    keep: `${member}'s worktree stays`,
    save: `Saved to a rescue branch; ${member}'s worktree removed`,
    force: `${member}'s worktree removed with its files`,
  })[a];

/** The line after Delete agent went through with this answer for its worktree. */
export const deleteSaid = (a: WorktreeAnswer, agent: string): string =>
  ({
    keep: `${agent} deleted; its worktree stays`,
    save: `${agent} deleted; its work is saved to a rescue branch`,
    force: `${agent} deleted; its worktree removed with its files`,
  })[a];
