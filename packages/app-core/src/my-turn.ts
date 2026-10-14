// What waits on the person, as one list of cards (docs/story9-my-turn.md 9-1): unread inbox items that need an answer, and agents that
// say `needs_you`. Built only from fields the web client already reads; priority and deadline come from the item's task.
import type { AgentView, InboxItem, Task } from '@handline/client';
import { isPerson } from './chat.ts';

export type CardKind = 'decision' | 'approval' | 'accept' | 'dialog' | 'attention';

export type CardAction =
  /** A written reply to the item (inbox.reply). */
  | { type: 'answer'; id: number }
  | { type: 'allow' | 'deny'; agent: string; prompt: string; choice: string }
  | { type: 'accept'; task: number }
  | { type: 'reject'; task: number }
  | { type: 'done'; item: InboxItem }
  | { type: 'open'; to: string }
  | { type: 'terminal'; agent: string }
  | { type: 'read'; id: number };

export interface Card {
  key: string;
  kind: CardKind;
  title: string;
  detail: string;
  agent: string | null;
  task: number | null;
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

  for (const i of items) {
    if (i.read) continue;
    const base = {
      agent: i.agent,
      task: i.task,
      since: at(i.created),
      ...when(i.task),
      detail: i.body && i.body !== i.title ? i.body : '',
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
        title: i.title,
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
      const decide = i.kind === 'decision' && i.task != null ? [{ type: 'done', item: i } as const] : [];
      cards.push({
        ...base,
        key: `item:${i.id}`,
        kind: 'decision',
        title: i.title,
        primary: 1,
        actions: [{ type: 'answer', id: i.id }, ...decide, ...open],
      });
    }
  }

  for (const t of tasks) {
    if (t.state !== 'reported' || !t.report || !isPerson(t.from, me)) continue; // only the person it answers to can accept
    cards.push({
      key: `accept:${t.id}`,
      kind: 'accept',
      title: t.title,
      detail: t.report.evidence,
      agent: t.to,
      task: t.id,
      ...when(t.id),
      since: at(t.report.at),
      primary: 1,
      actions: [
        { type: 'accept', task: t.id },
        { type: 'reject', task: t.id },
        { type: 'open', to: `#/tasks/${t.id}` },
      ],
    });
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
      task: a.task?.id ?? null,
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
