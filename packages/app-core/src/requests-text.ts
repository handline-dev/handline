// The words of a Requests item, one set for the PC and the phone: its group, where it came from, when it is due and what its buttons say.
import type { Card, CardAction } from '#src/my-turn.ts';

export type RequestGroup = 'Allow' | 'Decide' | 'Review' | 'Do by hand';
export const REQUEST_GROUPS: RequestGroup[] = ['Allow', 'Decide', 'Review', 'Do by hand'];
/** What the person must do: allow a request, decide a choice, review a result, or do something by hand. */
export const requestGroup = (c: Card): RequestGroup =>
  c.review ? 'Review' : c.kind === 'approval' ? 'Allow' : c.kind === 'decision' ? 'Decide' : 'Do by hand';

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

export function cardActionLabel(a: CardAction): string {
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
