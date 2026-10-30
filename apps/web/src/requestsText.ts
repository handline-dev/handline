// The words of the Requests page: where an item came from, when it is due, its buttons and the sentences of a worktree answer.
import type { Card, CardAction } from '@handline/app-core';

/** Where an item came from: the word the list shows, and the words the open item's chip carries. */
export const sourceWord = (c: Card): string =>
  c.memo != null ? 'Comment' : c.task != null ? 'Task' : c.kind === 'machine' ? 'Machine' : 'Session';
export const sourceFull = (c: Card): string =>
  c.task != null
    ? `${sourceWord(c)} T${c.task}`
    : c.kind === 'machine'
      ? 'Machine'
      : `Session ${(c.from ?? c.agent ?? '').split('.').pop()}`;

export function dueText(due: number, now: number): string {
  const s = Math.round((due - now) / 1000);
  const t =
    Math.abs(s) < 3600
      ? `${Math.max(1, Math.round(Math.abs(s) / 60))}m`
      : Math.abs(s) < 86400
        ? `${Math.round(Math.abs(s) / 3600)}h`
        : `${Math.round(Math.abs(s) / 86400)}d`;
  return s >= 0 ? `due in ${t}` : `${t} overdue`;
}

export function label(a: CardAction): string {
  switch (a.type) {
    case 'answer':
      return 'Reply';
    case 'allow':
      return 'Allow';
    case 'deny':
      return 'Deny';
    case 'done':
    case 'done_task':
      return 'Done';
    case 'worktree':
      return { keep: 'Keep it', save: 'Save to a branch, then remove', force: 'Remove anyway' }[a.answer];
    case 'approve':
      return 'Approve';
    case 'ask_change':
    case 'change':
      return 'Ask for change';
    case 'reply':
      return a.label;
    case 'open':
      return 'Open';
    case 'terminal':
      return 'Open the terminal';
    case 'retry':
      return 'Try again';
    case 'read':
      return 'Mark read';
  }
}

export const memberOf = (detail: string) => /^Member: (.+)$/m.exec(detail)?.[1]?.trim() ?? 'the member';
/** A worktree-removal body ends with how to close the task by number; the buttons do that. */
export const factsOf = (detail: string) => detail.split(/\n+Member: /)[0] ?? detail;
export const WORKTREE_SAID = {
  keep: (m: string) => `${m}'s worktree stays`,
  save: (m: string) => `Saved to a rescue branch; ${m}'s worktree removed`,
  force: (m: string) => `${m}'s worktree removed with its files`,
};
