// My turn (#/my-turn, docs/story9-my-turn.md 9-1): everything that waits on the person as cards, most urgent first. A card has its primary
// button(s) and a menu for the rest; a dialog the daemon does not know has only Open terminal.
import { useMemo, useState } from 'react';
import { ago, myTurn, type Card, type CardAction } from '@handline/app-core';
import { useAgents, useInbox, useTasks, useWrite } from '@handline/app-data';
import { goTerminal } from './route.ts';
import { ReasonSheet } from './taskui.tsx';
import { Button, Icon, Input } from './ui/index.ts';

/** The cards the person's turn holds: unread items, agents that need them, reports to accept. */
export function useMyTurn(): { cards: Card[]; loading: boolean } {
  const inbox = useInbox();
  const agents = useAgents();
  const tasks = useTasks({});
  const items = inbox.data?.items;
  const list = agents.data?.agents;
  const rows = tasks.data?.items;
  const cards = useMemo(() => myTurn({ items: items ?? [], agents: list ?? [], tasks: rows ?? [] }), [items, list, rows]);
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

function CardView({ card }: { card: Card }) {
  const answer = useWrite('inbox.reply');
  const prompt = useWrite('prompt.answer');
  const accept = useWrite('task.accept');
  const reject = useWrite('task.reject');
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const [box, setBox] = useState(false);
  const [text, setText] = useState('');
  const [back, setBack] = useState(false);
  const [menu, setMenu] = useState(false);
  const run = (a: CardAction) => {
    setMenu(false);
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
    <article className="my-turn-card rounded-card border border-line bg-panel px-3 py-2.5" data-kind={card.kind} data-card={card.key}>
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
      {card.detail ? <p className="m-0 mt-1 line-clamp-3 text-ink-2 [overflow-wrap:anywhere]">{card.detail}</p> : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {main.map((a, i) => (
          <Button key={a.type + i} variant={i === 0 ? 'solid' : 'outline'} label={label(a)} onClick={() => run(a)}>
            {label(a)}
          </Button>
        ))}
        {rest.length ? (
          <span className="relative">
            <Button label="More" aria-haspopup="menu" aria-expanded={menu} icon="menu" onClick={() => setMenu(!menu)} />
            {menu ? (
              <span
                role="menu"
                className="absolute right-0 z-10 mt-1 flex min-w-[160px] flex-col rounded-card border border-line bg-panel py-1 shadow-[var(--shadow-pop)]"
              >
                {rest.map((a, i) => (
                  <button key={a.type + i} role="menuitem" className="h-8 px-3 text-left text-body hover:bg-raised" onClick={() => run(a)}>
                    {label(a)}
                  </button>
                ))}
              </span>
            ) : null}
          </span>
        ) : null}
      </div>
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
  return (
    <section className="my-turn mx-auto h-full max-w-[760px] overflow-auto px-4 pb-6" aria-label="My turn">
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
            <CardView key={c.key} card={c} />
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
}
