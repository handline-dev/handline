// The words of the Requests page: the shared ones (group, source, due, buttons) come from app-core; the sentences of a worktree answer are here.
export { sourceWord, sourceFull, dueText, cardActionLabel as label } from '@handline/app-core';

export const memberOf = (detail: string) => /^Member: (.+)$/m.exec(detail)?.[1]?.trim() ?? 'the member';
/** A worktree-removal body ends with how to close the task by number; the buttons do that. */
export const factsOf = (detail: string) => detail.split(/\n+Member: /)[0] ?? detail;
export const WORKTREE_SAID = {
  keep: (m: string) => `${m}'s worktree stays`,
  save: (m: string) => `Saved to a rescue branch; ${m}'s worktree removed`,
  force: (m: string) => `${m}'s worktree removed with its files`,
};
