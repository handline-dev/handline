// What waits on the person, as one list of cards (docs/story9-my-turn.md 9-1): unread inbox items that need an answer, and agents that
// say `needs_you`, and open tasks given to the person. Built only from fields the web client already reads; priority and deadline come from the item's task.
import type { AgentView, InboxItem, Task } from '@handline/client';
import { isPerson } from '#src/chat.ts';
import { CLOSED } from '#src/task-table.ts';

export type CardKind = 'decision' | 'approval' | 'dialog' | 'attention' | 'task';

export type CardAction =
  /** A written reply to the item (inbox.reply). */
  | { type: 'answer'; id: number }
  | { type: 'allow' | 'deny'; agent: string; prompt: string; choice: string }
  | { type: 'done'; item: InboxItem }
  /** A task given to the person with no inbox item: done 'Done'. */
  | { type: 'done_task'; task: number }
  /** A review card: done 'Approved'. */
  | { type: 'approve'; item: InboxItem }
  /** A review card: the UI asks for text and a drawing, comments them, then closes the task 'Change asked: <text>'. */
  | { type: 'ask_change'; item: InboxItem }
  /** An ask raised by a task comment: a fixed reply (OK, Approved) answers it as a comment on the task, not task.done. */
  | { type: 'reply'; id: number; text: string; label: string }
  /** The same ask on a review: the UI asks for text and replies `Change asked: <text>`. */
  | { type: 'change'; id: number }
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
  /** The task comment an ask answers (InboxItem.memo); null for every other card. */
  memo: number | null;
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
/** What a review card closes its task with. */
export const APPROVED = 'Approved';
export const changeAsked = (text: string) => `Change asked: ${text.trim()}`;

/** The pictures of a review: the images the first comment names, as attachment ids with the name to show. */
export function reviewPictures(
  memos: readonly { id: number; parts?: readonly object[]; files?: readonly { id: string; kind: string; name?: string }[] }[],
) {
  const first = memos.reduce<(typeof memos)[number] | undefined>((a, m) => (!a || m.id < a.id ? m : a), undefined);
  const named = (first?.parts ?? []).flatMap((p) => {
    const { image, gone } = p as { image?: { id: string; name?: string }; gone?: boolean };
    return image && !gone ? [{ id: image.id, name: image.name ?? 'picture' }] : [];
  });
  if (named.length) return named;
  return (first?.files ?? []).filter((f) => f.kind === 'image').map((f) => ({ id: f.id, name: f.name ?? 'picture' }));
}
const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN);
const at = (iso: string | null | undefined) => (Number.isFinite(ms(iso)) ? ms(iso) : 0);

/** The agents that have a card in the person's turn: the filter that reads 'My turn'. */
export const turnAgents = (cards: readonly Card[]): Set<string> => new Set(cards.flatMap((c) => (c.agent ? [c.agent] : [])));

/** The open tasks given to the person that no unread item already stands for; a task leaves when it is done, discarded or handed on. */
function taskCards(
  tasks: readonly Task[],
  shown: ReadonlySet<number>,
  me: string,
  when: (task: number | null) => Pick<Card, 'priority' | 'due'>,
): Card[] {
  return tasks
    .filter((t) => !CLOSED.includes(t.state) && isPerson(t.to, me) && !shown.has(t.id))
    .map((t) => ({
      key: `task:${t.id}`,
      kind: 'task',
      title: t.title,
      detail: '',
      agent: null,
      from: t.from && !isPerson(t.from, me) ? t.from : null,
      task: t.id,
      memo: null,
      review: !!t.tags?.includes(REVIEW_TAG),
      ...when(t.id),
      since: at(t.created),
      primary: 2,
      actions: [
        { type: 'done_task', task: t.id },
        { type: 'open', to: `#/tasks/${t.id}` },
      ],
    }));
}

/** An ask an agent raised by naming `@human` in a task comment: the founder's answer is a reply comment on that task, and the task stays open. */
function commentAsk(
  i: InboxItem,
  base: Omit<Card, 'key' | 'kind' | 'title' | 'primary' | 'actions'>,
  title: string,
  open: readonly CardAction[],
): Card {
  const answer = { type: 'answer', id: i.id } as const;
  const verbs: CardAction[] = base.review
    ? [{ type: 'reply', id: i.id, text: APPROVED, label: 'Approve' }, { type: 'change', id: i.id }, answer]
    : [{ type: 'reply', id: i.id, text: 'OK', label: 'Go ahead' }, answer];
  return {
    ...base,
    // The card says whom it asks; the mention in the text is noise.
    detail: base.detail.replace(/\s*@human\b[,:]?\s*/g, ' ').trim(),
    key: `item:${i.id}`,
    kind: 'decision',
    title,
    primary: verbs.length - (base.review ? 1 : 0),
    actions: [...verbs, ...open],
  };
}

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
      memo: i.memo ?? null,
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
    } else if (i.kind === 'ask' && i.memo != null && i.task != null) {
      cards.push(commentAsk(i, base, title, open));
    } else if (KINDS[i.kind]) {
      const decide: CardAction[] =
        i.kind !== 'decision' || i.task == null
          ? []
          : base.review
            ? [
                { type: 'approve', item: i },
                { type: 'ask_change', item: i },
              ]
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
      memo: null,
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

  cards.push(...taskCards(tasks, new Set(cards.flatMap((c) => (c.task == null ? [] : [c.task]))), me, when));

  // Until the founder's tasks are migrated, a task with an open `@human` ask is shown once: by the ask, not by its task or decision card.
  const asked = new Set(cards.flatMap((c) => (c.memo != null && c.task != null ? [c.task] : [])));
  const once = cards.filter((c) => c.memo != null || c.task == null || !asked.has(c.task) || (c.kind !== 'decision' && c.kind !== 'task'));

  return once.sort(
    (x, y) =>
      x.priority - y.priority ||
      (x.due === y.due ? 0 : x.due == null ? 1 : y.due == null ? -1 : x.due - y.due) ||
      x.since - y.since ||
      x.key.localeCompare(y.key),
  );
}

/** An agent with messages for the person they have not seen: the Messages section of My turn, one row each. */
export interface MessageRow {
  agent: string;
  /** The whole body of its newest request to the founder. */
  line: string;
  /** When it was written (ms). */
  at: number;
  unread: number;
  /** The message to open the chat at (the first above the founder's cursor), absent from a daemon without founder_seen. */
  from: number | null;
}

/** One row per agent with unread messages, newest first. */
export function messageRows(agents: readonly AgentView[]): MessageRow[] {
  return agents
    .filter((a) => (a.founder_unread ?? 0) > 0)
    .map((a) => ({
      agent: a.name,
      line: a.last_to_founder?.text ?? '',
      at: at(a.last_to_founder?.at),
      unread: a.founder_unread!,
      from: a.founder_seen === undefined ? null : a.founder_seen + 1,
    }))
    .sort((x, y) => y.at - x.at || x.agent.localeCompare(y.agent));
}

export type InboxGroup = 'decisions' | 'approvals' | 'fyi';

/** The Inbox's three groups (docs/story9-my-turn.md 9-2), mapped on the client from `kind` and `prompt`: questions the agent cannot go on without, permission prompts, everything else. */
export function inboxGroup(i: Pick<InboxItem, 'kind' | 'prompt'>): InboxGroup {
  if (i.prompt?.kind === 'permission') return 'approvals';
  return KINDS[i.kind] ? 'decisions' : 'fyi';
}
