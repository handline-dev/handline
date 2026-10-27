// My turn (#/my-turn, docs/story9-my-turn.md 9-1): everything that waits on the person as cards, most urgent first. A card has its primary
// button(s) and a menu for the rest; a dialog the daemon does not know has only Open terminal.
import { useState, type KeyboardEvent } from 'react';
import { APPROVED, ago, noteIsLong, type Card, type CardAction } from '@handline/app-core';
import { classify, sentence, useMyTurn, useWrite } from '@handline/app-data';
import { Fold } from '#src/Fold.tsx';
import { goTerminal } from '#src/route.ts';
import { useWide } from '#src/taskui.tsx';
import { AskChange, ReviewPictures } from '#src/ReviewCard.tsx';
import { TaskDetail } from '#src/TaskDetail.tsx';
import { Button, Icon, Input, MenuButton, MenuItem, PageHead, PageTitle, ScrollPage } from '#src/ui/index.ts';
import { useNow } from '#src/Gauge.tsx';

const KIND: Record<Card['kind'], string> = {
  decision: 'Decision',
  approval: 'Approve',
  dialog: 'Dialog',
  attention: 'Needs you',
  task: 'Task',
};

/** Outside the component: the compiler reads an assignment to `location` in a handler as a mutation of a value it owns. */
const goHash = (to: string) => void (location.hash = to);

function dueText(due: number, now: number): string {
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
    case 'done':
    case 'done_task':
      return 'Done';
    case 'approve':
      return 'Approve';
    case 'ask_change':
      return 'Ask for change';
    case 'open':
      return 'Open';
    case 'terminal':
      return 'Open terminal';
    case 'read':
      return 'Mark read';
  }
}

function CardView({ card, selected, onOpen }: { card: Card; selected: boolean; onOpen: (task: number) => void }) {
  const now = useNow();
  const answer = useWrite('inbox.reply');
  const prompt = useWrite('prompt.answer');
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const [box, setBox] = useState(false);
  const [asking, setAsking] = useState(false);
  const [text, setText] = useState('');
  // The daemon's own words for a refused write; the buttons stay usable.
  const refused = [answer, prompt, done, read].find((w) => w.isError)?.error;
  const run = (a: CardAction) => {
    switch (a.type) {
      case 'answer':
        return setBox(true);
      case 'allow':
      case 'deny':
        return prompt.mutate({ agent: a.agent, prompt: a.prompt, choice: a.choice });
      case 'done':
        return void (done.mutate({ task: a.item.task!, evidence: 'decided in My turn', reason: true }), read.mutate({ ids: [a.item.id] }));
      case 'done_task':
        return done.mutate({ task: a.task, evidence: 'Done', reason: true });
      case 'approve':
        return void (done.mutate({ task: a.item.task!, evidence: APPROVED, reason: true }), read.mutate({ ids: [a.item.id] }));
      case 'ask_change':
        return setAsking(true);
      case 'open':
        return card.task != null ? onOpen(card.task) : goHash(a.to);
      case 'terminal':
        return goTerminal(a.agent);
      case 'read':
        return read.mutate({ ids: [a.id] });
    }
  };
  // The rarer verbs are a three-dot menu at the card's top-right, and so is Reply unless it is the card's main verb: then it stays a button.
  const main = card.actions.slice(0, card.primary);
  const rest = card.actions.slice(card.primary).filter((a) => a.type !== 'answer');
  const answerId = card.actions.find((a) => a.type === 'answer');
  const replyIcon = answerId && !main.includes(answerId) ? answerId : undefined;
  const askId = card.actions.find((a) => a.type === 'ask_change')?.item.id;
  return (
    <article
      className={`my-turn-card group rounded-card border bg-panel px-3 py-2.5 ${selected ? 'border-primary' : 'border-line'}`}
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
          <span className={`text-hint ${card.review ? 'text-ok' : 'text-ink-2'}`}>{card.review ? 'Review' : KIND[card.kind]}</span>
          <h3 className="m-0 min-w-0 flex-1 truncate text-[15px] font-medium">{card.title}</h3>
          <span
            className="flex items-center self-center opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {replyIcon ? <Button size="sm" icon="reply" label="Reply" onClick={() => run(replyIcon)} /> : null}
            {rest.length ? (
              <MenuButton size="sm" icon="ellipsis" label="More">
                {rest.map((a, i) => (
                  <MenuItem key={a.type + i} onSelect={() => run(a)}>
                    {label(a)}
                  </MenuItem>
                ))}
              </MenuButton>
            ) : null}
          </span>
        </header>
        <p className="muted m-0 mt-0.5 flex flex-wrap gap-x-3 text-hint">
          {card.from ? (
            <span>
              from <span className="mono">{card.from}</span>
            </span>
          ) : null}
          {card.task != null ? <span className="mono">T{card.task}</span> : null}
          {card.due != null ? <span>{dueText(card.due, now)}</span> : null}
          {card.since ? <span>waiting {ago(new Date(card.since).toISOString(), now)}</span> : null}
        </p>
        {card.detail ? (
          <div className="mt-1 text-ink-2 [overflow-wrap:anywhere]">
            <Fold note={card.detail} long={card.task != null && noteIsLong(card.detail)}>
              {(text) => <p className={`m-0${card.task != null ? '' : ' whitespace-pre-wrap'}`}>{text}</p>}
            </Fold>
          </div>
        ) : null}
        {card.review && card.task != null ? <ReviewPictures task={card.task} /> : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {main.map((a, i) => (
          <Button key={a.type + i} variant={i === 0 ? 'solid' : 'outline'} label={label(a)} onClick={() => run(a)}>
            {label(a)}
          </Button>
        ))}
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
          <Input
            grow
            autoFocus
            aria-label="Your reply"
            placeholder={card.from ? `Reply to ${card.from}` : 'Reply'}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <Button label="Send reply" type="submit" disabled={!text.trim()}>
            <Icon name="reply" aria-hidden /> Send
          </Button>
        </form>
      ) : null}
      {asking && card.task != null && askId ? <AskChange task={card.task} item={askId} onClose={() => setAsking(false)} /> : null}
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
    <ScrollPage beside={open !== null} className="my-turn" aria-label="My turn">
      <PageHead>
        <PageTitle>
          My turn <span className="muted">{cards.length}</span>
        </PageTitle>
      </PageHead>
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
          <strong>Nothing needs you</strong>
          <p>Messages for you and tasks given to you show here.</p>
        </div>
      )}
    </ScrollPage>
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
