// My turn (#/my-turn, docs/story9-my-turn.md 9-1): everything that waits on the person as cards, most urgent first. A card has its primary
// button(s) and a menu for the rest; a dialog the daemon does not know has only Open terminal.
import { useState } from 'react';
import { APPROVED, ago, changeAsked, noteIsLong, type Card, type CardAction, type MessageRow } from '@handline/app-core';
import { classify, sentence, useMyTurn, useWrite } from '@handline/app-data';
import { Fold } from '#src/Fold.tsx';
import { goMessage, goTerminal } from '#src/route.ts';
import { useWide } from '#src/taskui.tsx';
import { AskChange, ReviewPictures } from '#src/ReviewCard.tsx';
import { TaskDetail } from '#src/TaskDetail.tsx';
import { Button, Icon, Input, MenuButton, MenuItem, PageHead, PageTitle, ScrollPage } from '#src/ui/index.ts';
import { Button as DsButton } from '#src/ds/Button.tsx';
import { Chip } from '#src/ds/Chip.tsx';
import { Answer, ItemHeader } from '#src/ds/Item.tsx';
import { SeeInFull, Who } from '#src/ds/Link.tsx';
import { ListRow, SectionHead } from '#src/ds/ListRow.tsx';
import { useNow } from '#src/Gauge.tsx';

/** Where an item came from, the word on its chip. */
const sourceOf = (c: Card): string => (c.review ? 'Review' : c.task != null ? 'Task' : c.kind === 'machine' ? 'Machine' : 'Chat');

const KIND: Record<Card['kind'], string> = {
  decision: 'Decision',
  approval: 'Approve',
  dialog: 'Dialog',
  attention: 'Needs you',
  machine: 'Machine',
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
    case 'change':
      return 'Ask for change';
    case 'reply':
      return a.label;
    case 'open':
      return 'Open';
    case 'terminal':
      return 'Open terminal';
    case 'read':
      return 'Mark read';
  }
}

/** The open item: its header, what it says, then the one answer with where to see it in full. A row only opens; an answer is given here. */
function CardDetail({ card, onOpen }: { card: Card; onOpen: (task: number) => void }) {
  const now = useNow();
  const answer = useWrite('inbox.reply');
  const prompt = useWrite('prompt.answer');
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const [box, setBox] = useState<false | 'reply' | 'change'>(false);
  const [asking, setAsking] = useState(false);
  const [text, setText] = useState('');
  // The daemon's own words for a refused write; the buttons stay usable.
  const refused = [answer, prompt, done, read].find((w) => w.isError)?.error;
  const run = (a: CardAction) => {
    switch (a.type) {
      case 'answer':
        return setBox('reply');
      case 'change':
        return setBox('change');
      case 'reply':
        return answer.mutate({ id: a.id, text: a.text });
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
  const kind = card.review ? 'Review' : card.memo != null ? 'Question for you' : KIND[card.kind];
  const full =
    card.task != null ? (
      <SeeInFull onClick={() => onOpen(card.task!)} title={`Open T${card.task}`}>
        Open T{card.task}
      </SeeInFull>
    ) : card.from ? (
      <SeeInFull onClick={() => goHash(`#/agents/${encodeURIComponent(card.from!)}`)}>View in chat</SeeInFull>
    ) : undefined;
  return (
    <article className="my-turn-card px-5 py-4" data-kind={card.kind} data-card={card.key}>
      <ItemHeader
        source={<Chip>{sourceOf(card)}</Chip>}
        who={card.from ? <Who onClick={() => goHash(`#/agents/${encodeURIComponent(card.from!)}`)}>{card.from}</Who> : undefined}
        when={
          [card.due != null ? dueText(card.due, now) : '', card.since ? `waiting ${ago(new Date(card.since).toISOString(), now)}` : '']
            .filter(Boolean)
            .join(' · ') || 'now'
        }
        title={card.title}
      />
      {card.review && card.task != null ? <ReviewPictures task={card.task} /> : null}
      <Answer
        kind={kind}
        code={card.kind === 'approval' || card.kind === 'dialog'}
        ask={
          card.detail ? (
            <Fold note={card.detail} long={card.task != null && noteIsLong(card.detail)}>
              {(text) => <p className={`m-0${card.task != null ? '' : ' whitespace-pre-wrap'}`}>{text}</p>}
            </Fold>
          ) : null
        }
        full={
          <>
            {rest.length ? (
              <MenuButton size="sm" icon="ellipsis" label="More">
                {rest.map((a, i) => (
                  <MenuItem key={a.type + i} onSelect={() => run(a)}>
                    {label(a)}
                  </MenuItem>
                ))}
              </MenuButton>
            ) : null}
            {full}
          </>
        }
      >
        {replyIcon ? <DsButton size="m" label="Reply" onClick={() => run(replyIcon)} /> : null}
        {main.map((a, i) => (
          <DsButton key={a.type + i} kind={i === 0 ? 'primary' : 'secondary'} label={label(a)} onClick={() => run(a)} />
        ))}
      </Answer>
      {refused ? (
        <p className="m-0 mt-1 text-xs text-bad" role="alert">
          {sentence(classify(refused))}
        </p>
      ) : null}
      {box && answerId?.type === 'answer' ? (
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            answer.mutate({ id: answerId.id, text: box === 'change' ? changeAsked(text) : text.trim() });
            setBox(false);
            setText('');
          }}
        >
          <Input
            grow
            autoFocus
            aria-label="Your reply"
            placeholder={box === 'change' ? 'What to change' : card.from ? `Reply to ${card.from}` : 'Reply'}
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

/** An agent with messages the person has not seen: its name, its newest line, when, and how many; a click opens the chat at the first unread. */
function RowView({ row }: { row: MessageRow }) {
  const now = useNow();
  return (
    <div data-row={row.agent}>
      <ListRow
        title={
          <>
            <span className="font-mono">{row.agent}</span> <span className="text-ink-2">{row.line}</span>
          </>
        }
        chip={<Chip kind="unread">{row.unread}</Chip>}
        time={row.at ? ago(new Date(row.at).toISOString(), now) : undefined}
        label={`Open the chat with ${row.agent}`}
        onOpen={() => (row.from === null ? (location.hash = `#/agents/${encodeURIComponent(row.agent)}`) : goMessage(row.agent, row.from))}
      />
    </div>
  );
}

export function MyTurnPage() {
  const { cards, rows, loading } = useMyTurn();
  const wide = useWide(1100);
  const now = useNow();
  const [pick, setPick] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  // Wide: the first item is open, as the list is read top down. Narrow: the list alone until a row is picked.
  const sel = cards.find((c) => c.key === pick) ?? (wide ? cards[0] : undefined);
  const back = () => (setOpen(null), setPick(null));
  if (!wide && open !== null) return <TaskDetail id={open} onBack={() => setOpen(null)} />;
  if (!wide && sel) return <ItemPage card={sel} onBack={back} onOpen={setOpen} />;
  const list = (
    <ScrollPage beside={wide} className="my-turn" aria-label="My turn">
      <PageHead>
        <PageTitle>
          My turn <span className="muted">{cards.length}</span>
        </PageTitle>
      </PageHead>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : cards.length || rows.length ? (
        <>
          {cards.length ? (
            <section aria-label="Needs you">
              <SectionHead count={cards.length}>Needs you</SectionHead>
              {cards.map((c) => (
                <div key={c.key} className="my-turn-card-row" data-card-row={c.key}>
                  <ListRow
                    title={c.title}
                    chip={<Chip>{sourceOf(c)}</Chip>}
                    time={c.since ? ago(new Date(c.since).toISOString(), now) : undefined}
                    selected={sel?.key === c.key}
                    label={c.task != null ? `Open T${c.task}` : c.title}
                    onOpen={() => (setPick(c.key), setOpen(null))}
                  />
                </div>
              ))}
            </section>
          ) : null}
          {rows.length ? (
            <section aria-label="Messages">
              <SectionHead count={rows.length}>Messages</SectionHead>
              {rows.map((r) => (
                <RowView key={r.agent} row={r} />
              ))}
            </section>
          ) : null}
        </>
      ) : (
        <div className="empty">
          <strong>Nothing needs you</strong>
          <p>Messages for you and tasks given to you show here.</p>
        </div>
      )}
    </ScrollPage>
  );
  if (!wide) return list;
  return (
    <div className="my-turn-split flex h-full min-h-0 min-w-0 flex-1">
      <div className="flex min-h-0 w-[min(420px,40%)] flex-none flex-col">{list}</div>
      <div className="min-h-0 min-w-0 flex-1 overflow-auto border-l border-line">
        {open !== null ? (
          <TaskDetail id={open} onBack={() => setOpen(null)} embedded />
        ) : sel ? (
          <CardDetail card={sel} onOpen={setOpen} />
        ) : null}
      </div>
    </div>
  );
}

/** A narrow window: the picked item takes the page, with one Back to the list. */
function ItemPage({ card, onBack, onOpen }: { card: Card; onBack: () => void; onOpen: (task: number) => void }) {
  return (
    <ScrollPage aria-label="My turn item">
      <PageHead back={{ label: 'My turn', to: onBack }} />
      <CardDetail card={card} onOpen={onOpen} />
    </ScrollPage>
  );
}
