// My turn (#/my-turn, docs/story9-my-turn.md 9-1): everything that waits on the person as cards, most urgent first. A card has its primary
// button(s) and a menu for the rest; a dialog the daemon does not know has only Open terminal.
import { useMemo, useState, type KeyboardEvent } from 'react';
import { ago, myTurn, type Card, type CardAction } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { classify, sentence, useAgents, useInbox, useTasks, useWrite } from '@handline/app-data';
import { goTerminal } from './route.ts';
import { ReasonSheet, useWide } from './taskui.tsx';
import { TaskDetail } from './TaskDetail.tsx';
import { Button, Icon, Input, MenuButton, MenuItem } from './ui/index.ts';

/** The cards the person's turn holds: unread items, agents that need them, reports to accept. */
export function useMyTurn(): { cards: Card[]; loading: boolean } {
  const inbox = useInbox();
  const agents = useAgents();
  const tasks = useTasks({});
  const me = useStore().me;
  const items = inbox.data?.items;
  const list = agents.data?.agents;
  const rows = tasks.data?.items;
  const cards = useMemo(() => myTurn({ items: items ?? [], agents: list ?? [], tasks: rows ?? [], me }), [items, list, rows, me]);
  return { cards, loading: inbox.isPending && agents.isPending };
}

const KIND: Record<Card['kind'], string> = {
  decision: 'Decision',
  approval: 'Approve',
  accept: 'Accept',
  dialog: 'Dialog',
  attention: 'Needs you',
};

function dueText(due: number, now = Date.now()): string {
  const s = Math.round((due - now) / 1000);
  const t =
    Math.abs(s) < 3600
      ? `${Math.max(1, Math.round(Math.abs(s) / 60))}m`
      : Math.abs(s) < 86400
        ? `${Math.round(Math.abs(s) / 3600)}h`
        : `${Math.round(Math.abs(s) / 86400)}d`;
  return s >= 0 ? `due in ${t}` : `${t} overdue`;
}

function label(a: CardAction): string {
  switch (a.type) {
    case 'answer':
      return 'Reply';
    case 'allow':
      return 'Allow';
    case 'deny':
      return 'Deny';
    case 'accept':
      return 'Accept';
    case 'reject':
      return 'Send back';
    case 'done':
      return 'Done';
    case 'open':
      return 'Open';
    case 'terminal':
      return 'Open terminal';
    case 'read':
      return 'Mark read';
  }
}

function CardView({ card, selected, onOpen }: { card: Card; selected: boolean; onOpen: (task: number) => void }) {
  const answer = useWrite('inbox.reply');
  const prompt = useWrite('prompt.answer');
  const accept = useWrite('task.accept');
  const reject = useWrite('task.reject');
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const [box, setBox] = useState(false);
  const [text, setText] = useState('');
  const [back, setBack] = useState(false);
  // The daemon's own words for a refused write; the buttons stay usable.
  const refused = [answer, prompt, accept, reject, done, read].find((w) => w.isError)?.error;
  const run = (a: CardAction) => {
    switch (a.type) {
      case 'answer':
        return setBox(true);
      case 'allow':
      case 'deny':
        return prompt.mutate({ agent: a.agent, prompt: a.prompt, choice: a.choice });
      case 'accept':
        return accept.mutate({ task: a.task });
      case 'reject':
        return setBack(true);
      case 'done':
        return void (done.mutate({ task: a.item.task!, evidence: 'decided in My turn', reason: true }), read.mutate({ ids: [a.item.id] }));
      case 'open':
        return void (location.hash = a.to);
      case 'terminal':
        return goTerminal(a.agent);
      case 'read':
        return read.mutate({ ids: [a.id] });
    }
  };
  const main = card.actions.slice(0, card.primary);
  const rest = card.actions.slice(card.primary);
  const answerId = card.actions.find((a) => a.type === 'answer');
  return (
    <article
      className={`my-turn-card rounded-card border bg-panel px-3 py-2.5 ${selected ? 'border-primary' : 'border-line'}`}
      data-kind={card.kind}
      data-card={card.key}
      aria-current={selected || undefined}
    >
      {/* A row with a task opens it in the detail beside the list; a card with no task shows its whole text and has nothing to open. */}
      <div
        {...(card.task != null
          ? {
              role: 'button',
              tabIndex: 0,
              'aria-label': `Open T${card.task}`,
              onClick: () => onOpen(card.task!),
              onKeyDown: (e: KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onOpen(card.task!);
                }
              },
            }
          : {})}
        className={`rounded-card outline-offset-2${card.task != null ? ' cursor-pointer' : ''}`}
      >
        <header className="flex min-w-0 items-baseline gap-2">
          {card.priority <= 3 ? (
            <span className="mono text-hint text-ink-2" title="Priority">
              P{card.priority}
            </span>
          ) : null}
          <span className="text-hint text-ink-2">{KIND[card.kind]}</span>
          <h3 className="m-0 min-w-0 flex-1 truncate text-[15px] font-medium">{card.title}</h3>
        </header>
        <p className="muted m-0 mt-0.5 flex flex-wrap gap-x-3 text-hint">
          {card.agent ? <span className="mono">{card.agent}</span> : null}
          {card.task != null ? <span className="mono">T{card.task}</span> : null}
          {card.due != null ? <span>{dueText(card.due)}</span> : <span>no deadline</span>}
          {card.since ? <span>waiting {ago(new Date(card.since).toISOString())}</span> : null}
        </p>
        {card.detail ? (
          <p className={`m-0 mt-1 text-ink-2 [overflow-wrap:anywhere]${card.task != null ? ' line-clamp-3' : ' whitespace-pre-wrap'}`}>
            {card.detail}
          </p>
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {main.map((a, i) => (
          <Button key={a.type + i} variant={i === 0 ? 'solid' : 'outline'} label={label(a)} onClick={() => run(a)}>
            {label(a)}
          </Button>
        ))}
        {rest.length ? (
          <MenuButton label="More" icon="menu">
            {rest.map((a, i) => (
              <MenuItem key={a.type + i} onSelect={() => run(a)}>
                {label(a)}
              </MenuItem>
            ))}
          </MenuButton>
        ) : null}
      </div>
      {refused ? (
        <p className="m-0 mt-1 text-hint text-bad" role="alert">
          {sentence(classify(refused))}
        </p>
      ) : null}
      {box && answerId?.type === 'answer' ? (
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            answer.mutate({ id: answerId.id, text: text.trim() });
            setBox(false);
            setText('');
          }}
        >
          <Input grow autoFocus aria-label="Your reply" value={text} onChange={(e) => setText(e.target.value)} />
          <Button label="Send reply" type="submit" disabled={!text.trim()}>
            <Icon name="reply" aria-hidden /> Send
          </Button>
        </form>
      ) : null}
      {back && card.task != null ? (
        <ReasonSheet
          label="What is missing"
          submit="Send back"
          onCancel={() => setBack(false)}
          onSubmit={(note) => {
            reject.mutate({ task: card.task!, note });
            setBack(false);
          }}
        />
      ) : null}
    </article>
  );
}

export function MyTurnPage() {
  const { cards, loading } = useMyTurn();
  const wide = useWide(1100);
  const [open, setOpen] = useState<number | null>(null);
  // Narrow: the task takes the page, with Back. Wide: it opens beside the list, as on the Tasks page.
  if (open !== null && !wide) return <TaskDetail id={open} onBack={() => setOpen(null)} />;
  const list = (
    <section
      className={`my-turn h-full min-w-0 flex-1 overflow-auto px-4 pb-6${open === null ? ' mx-auto max-w-[760px]' : ''}`}
      aria-label="My turn"
    >
      <header className="sticky top-0 z-1 bg-canvas py-3">
        <h2 className="m-0 text-[18px]">
          My turn <span className="muted">{cards.length}</span>
        </h2>
      </header>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : cards.length ? (
        <div className="flex flex-col gap-2">
          {cards.map((c) => (
            <CardView key={c.key} card={c} selected={c.task != null && c.task === open} onOpen={setOpen} />
          ))}
        </div>
      ) : (
        <div className="empty">
          <strong>Nothing waits on you</strong>
          <p>Decisions, approvals and reports to accept show here.</p>
        </div>
      )}
    </section>
  );
  if (open === null) return list;
  return (
    <div className="my-turn-split flex h-full min-h-0 min-w-0 flex-1">
      {list}
      <div className="flex min-h-0 w-[min(520px,45%)] flex-none border-l border-line">
        <TaskDetail id={open} onBack={() => setOpen(null)} embedded />
      </div>
    </div>
  );
}
