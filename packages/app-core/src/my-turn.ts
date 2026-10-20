// What waits on the person, as one list of cards (docs/story9-my-turn.md 9-1): unread inbox items that need an answer, and agents that
// say `needs_you`. Built only from fields the web client already reads; priority and deadline come from the item's task.
import type { AgentView, InboxItem, Task } from '@handline/client';
import { isPerson } from './chat.ts';

export type CardKind = 'decision' | 'approval' | 'dialog' | 'attention';

export type CardAction =
  /** A written reply to the item (inbox.reply). */
  | { type: 'answer'; id: number }
  | { type: 'allow' | 'deny'; agent: string; prompt: string; choice: string }
  | { type: 'done'; item: InboxItem }
  /** A review card: done 'Approved'. */
  | { type: 'approve'; item: InboxItem }
  /** A review card: the UI asks for text and a drawing, comments them, then closes the task 'Change asked: <text>'. */
  | { type: 'ask_change'; item: InboxItem }
  | { type: 'open'; to: string }
  | { type: 'terminal'; agent: string }
  | { type: 'read'; id: number };

export interface Card {
  key: string;
  kind: CardKind;
  title: string;
  detail: string;
  agent: string | null;
  /** Who is asking, never the person themselves. */
  from: string | null;
  task: number | null;
  /** The task is tagged `review`: its first comment's pictures are the thing to look at (Memo.parts), and Approve and Ask for change stand in for Done. */
  review: boolean;
  /** 1 is the most urgent; 4 for a card with no task. */
  priority: number;
  /** The task's deadline (ms), null without one. */
  due: number | null;
  /** When it began to wait (ms). */
  since: number;
  /** The first `primary` of these are buttons (an approval's Allow and Deny); the rest go in the menu. */
  actions: CardAction[];
  primary: number;
}

export interface MyTurnInput {
  items: readonly InboxItem[];
  agents: readonly AgentView[];
  tasks: readonly Task[];
  /** The caller's own name: a report is accepted by whoever it answers to, the founder (`human`) or this name. */
  me?: string;
}

const KINDS: Record<string, CardKind> = { decision: 'decision', ask: 'decision' };
const NO_PRIORITY = 4;
export const REVIEW_TAG = 'review';
const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN);
const at = (iso: string | null | undefined) => (Number.isFinite(ms(iso)) ? ms(iso) : 0);

/** The cards, most urgent first: task priority, then the earliest deadline (none last), then the one waiting longest. */
export function myTurn({ items, agents, tasks, me = '' }: MyTurnInput): Card[] {
  const byTask = new Map(tasks.map((t) => [t.id, t]));
  const when = (task: number | null) => {
    const t = task == null ? undefined : byTask.get(task);
    const end = ms(t?.time?.end);
    return { priority: t?.priority ?? NO_PRIORITY, due: Number.isFinite(end) ? end : null };
  };
  const cards: Card[] = [];
  const prompted = new Set<string>();

  const other = (name: string | null | undefined) => (name && !isPerson(name, me) ? name : null);

  for (const i of items) {
    if (i.read) continue;
    const t = i.task == null ? undefined : byTask.get(i.task);
    // The daemon titles an item with the first line of its body; a card about a task is titled with the task, and the body is its text.
    const title = t?.title ?? i.title;
    const base = {
      agent: i.agent,
      from: other(t?.from) ?? other(i.agent),
      task: i.task,
      review: !!t?.tags?.includes(REVIEW_TAG),
      since: at(i.created),
      ...when(i.task),
      detail: i.body && i.body !== title ? i.body : '',
    };
    const open: CardAction[] = [
      ...(i.task != null
        ? [{ type: 'open', to: `#/tasks/${i.task}` } as const]
        : i.agent
          ? [{ type: 'open', to: `#/agents/${encodeURIComponent(i.agent)}` } as const]
          : []),
      { type: 'read', id: i.id },
    ];
    if (i.prompt && i.prompt.kind === 'permission') {
      // Without yes/no options in the prompt the card has only Open terminal.
      const yes = i.prompt.options.find((o) => o.answer === 'yes');
      const no = i.prompt.options.find((o) => o.answer === 'no');
      if (i.agent) prompted.add(i.agent);
      cards.push({
        ...base,
        key: `item:${i.id}`,
        kind: 'approval',
        title,
        detail: i.prompt.command ?? i.prompt.reason ?? base.detail,
        primary: yes && no && i.agent ? 2 : 1,
        actions: [
          ...(yes && i.agent ? [{ type: 'allow', agent: i.agent, prompt: i.prompt.id, choice: yes.key } as const] : []),
          ...(no && i.agent ? [{ type: 'deny', agent: i.agent, prompt: i.prompt.id, choice: no.key } as const] : []),
          ...(i.agent ? [{ type: 'terminal', agent: i.agent } as const] : []),
          ...open,
        ],
      });
    } else if (KINDS[i.kind]) {
      const decide: CardAction[] =
        i.kind !== 'decision' || i.task == null
          ? []
          : base.review
            ? [{ type: 'approve', item: i }, { type: 'ask_change', item: i }]
            : [{ type: 'done', item: i }];
      // A card the daemon raised after its own recovery failed: its fix is the first button.
      const fix = i.fix === 'open_terminal' && i.agent ? [{ type: 'terminal', agent: i.agent } as const] : [];
      cards.push({
        ...base,
        key: `item:${i.id}`,
        kind: 'decision',
        title,
        primary: 1 + decide.length + fix.length,
        actions: [...fix, { type: 'answer', id: i.id }, ...decide, ...open],
      });
    }
  }

  for (const a of agents) {
    // A daemon that reads the screen sets has_dialog and serves no needs_you: the card is still a dialog.
    const n = a.needs_you ?? (a.has_dialog ? { kind: 'dialog', since: a.state_since, text: a.state_detail || null } : null);
    if (!n) continue;
    if (n.kind !== 'dialog' && prompted.has(a.name)) continue; // its inbox item is the card
    const dialog = n.kind === 'dialog';
    const w = when(a.task?.id ?? null);
    cards.push({
      key: `agent:${a.name}`,
      kind: dialog ? 'dialog' : 'attention',
      title: dialog
        ? 'Waiting on a dialog'
        : n.kind === 'permission'
          ? 'Waiting for a yes'
          : n.kind === 'question'
            ? 'Asked you a question'
            : 'Hit the session limit',
      detail: n.text ?? '',
      agent: a.name,
      from: a.name,
      task: a.task?.id ?? null,
      review: false,
      ...w,
      since: at(n.since),
      primary: 1,
      // A dialog the daemon does not know has no Allow or Deny: the terminal is the only way.
      actions: dialog
        ? [{ type: 'terminal', agent: a.name }]
        : [
            { type: 'open', to: `#/agents/${encodeURIComponent(a.name)}` },
            { type: 'terminal', agent: a.name },
          ],
    });
  }

  return cards.sort(
    (x, y) =>
      x.priority - y.priority ||
      (x.due === y.due ? 0 : x.due == null ? 1 : y.due == null ? -1 : x.due - y.due) ||
      x.since - y.since ||
      x.key.localeCompare(y.key),
  );
}

export type InboxGroup = 'decisions' | 'approvals' | 'fyi';

/** The Inbox's three groups (docs/story9-my-turn.md 9-2), mapped on the client from `kind` and `prompt`: questions the agent cannot go on without, permission prompts, everything else. */
export function inboxGroup(i: Pick<InboxItem, 'kind' | 'prompt'>): InboxGroup {
  if (i.prompt?.kind === 'permission') return 'approvals';
  return KINDS[i.kind] ? 'decisions' : 'fyi';
}
