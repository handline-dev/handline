// What the Inbox page and its cards share: the kinds' words and icons, the verbs a page offers, and where a click on a card goes.
import type { InboxItem } from '@handline/client';
import { isIcon, type IconName } from './ui/index.ts';

export const KIND_LABEL: Record<string, string> = {
  decision: 'Decision',
  ask: 'Question',
  say: 'Message',
  report: 'Result',
  result: 'Result',
  block: 'Block',
  prompt: 'Prompt',
};
export const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind;
/** The icon of a kind: `report` is a result; a kind the app has no icon for falls back to the message one. */
export const kindIcon = (kind: string): IconName => {
  const k = kind === 'report' ? 'result' : kind;
  return isIcon(k) ? k : 'say';
};

/** The verbs the page offers a card, each with the reason it is off (or null). */
export interface Verbs {
  read: (id: number) => void;
  dismiss: (id: number) => void;
  answer: (id: number, how: { option: string } | { text: string }) => void;
  reply: (id: number, text: string) => void;
  /** Why a verb is off: "daemon not connected", "this daemon does not serve inbox.reply"; null when it works. */
  off: Record<'read' | 'dismiss' | 'answer' | 'reply', string | null>;
}

export const answerable = (i: InboxItem) => i.kind === 'decision' || i.kind === 'ask';

/** Where a click on a card goes: the task it is about, else the agent's chat. */
export const originOf = (i: InboxItem): string | null =>
  i.task != null ? `#/tasks/${i.task}` : i.agent ? `#/agents/${encodeURIComponent(i.agent)}` : null;

/** The card's classes: the look, and the state words (`read`, `on`, `go`, `more`) the row's verbs and the tests read. */
export const cardClass = (s: { read: boolean; on: boolean; go: boolean; more: boolean }) =>
  [
    'inbox-card group/card rounded-ctl border border-line bg-panel',
    s.read && 'read',
    s.on && 'on outline-2 outline-offset-1 outline-focus',
    // The border falls to the current colour.
    s.go && 'go cursor-pointer hover:border-current',
    s.more && 'more',
  ]
    .filter(Boolean)
    .join(' ');
