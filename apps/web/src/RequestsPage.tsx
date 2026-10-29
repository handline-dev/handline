// The Requests page (#/requests): everything that waits on the person, grouped by what they must do (Allow, Decide, Review, Do by hand), as a list
// beside the open item. A row only opens the item; the answer is given in the detail, which ends with its answers and nothing after them.
import { ChevronRight, Plus } from 'lucide-react';
import { useState } from 'react';
import { APPROVED, WORKTREE_WORDS, ago, changeAsked, noteIsLong, type Card, type CardAction } from '@handline/app-core';
import { classify, sentence, useAgents, useMyTurn, useWrite } from '@handline/app-data';
import { Fold } from '#src/Fold.tsx';
import { NewTask } from '#src/NewTask.tsx';
import { goTasks, goTerminal } from '#src/route.ts';
import { useWide } from '#src/taskui.tsx';
import { AskChange, ReviewPictures } from '#src/ReviewCard.tsx';
import { Button, Icon, Input, MenuButton, MenuItem, PageHead, PageTitle, ScrollPage } from '#src/ui/index.ts';
import { Button as DsButton } from '#src/ds/Button.tsx';
import { Chip } from '#src/ds/Chip.tsx';
import { Answer, ItemHeader } from '#src/ds/Item.tsx';
import { Link, Who } from '#src/ds/Link.tsx';
import { ListRow, SectionHead } from '#src/ds/ListRow.tsx';
import { useNow } from '#src/Gauge.tsx';

type Group = 'Allow' | 'Decide' | 'Review' | 'Do by hand';
const GROUPS: Group[] = ['Allow', 'Decide', 'Review', 'Do by hand'];
/** What the person must do: allow a request, decide a choice, review a result, or do something by hand. */
const groupOf = (c: Card): Group =>
  c.review ? 'Review' : c.kind === 'approval' ? 'Allow' : c.kind === 'decision' ? 'Decide' : 'Do by hand';

/** Where an item came from: the word the list shows, and the words the open item's chip carries. */
const sourceWord = (c: Card): string =>
  c.memo != null ? 'Comment' : c.task != null ? 'Task' : c.kind === 'machine' ? 'Machine' : 'Session';
const sourceFull = (c: Card): string =>
  c.task != null
    ? `${sourceWord(c)} T${c.task}`
    : c.kind === 'machine'
      ? 'Machine'
      : `Session ${(c.from ?? c.agent ?? '').split('.').pop()}`;

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
    case 'worktree':
      return { keep: 'Keep it', save: 'Save a copy, then remove', force: 'Remove anyway' }[a.answer];
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

/** The open item: its header, the ask, then the answers and nothing after them. The source chip is the one way to open the item itself. */
function CardDetail({ card }: { card: Card }) {
  const now = useNow();
  const answer = useWrite('inbox.reply');
  const prompt = useWrite('prompt.answer');
  const done = useWrite('task.done');
  const read = useWrite('inbox.mark_read');
  const [change, setChange] = useState(false);
  const [asking, setAsking] = useState(false);
  const [text, setText] = useState('');
  // The daemon's own words for a refused write; the buttons stay usable.
  const refused = [answer, prompt, done, read].find((w) => w.isError)?.error;
  const run = (a: CardAction) => {
    switch (a.type) {
      case 'answer':
        return;
      case 'change':
        return setChange(true);
      case 'reply':
        return answer.mutate({ id: a.id, text: a.text });
      case 'allow':
      case 'deny':
        return prompt.mutate({ agent: a.agent, prompt: a.prompt, choice: a.choice });
      case 'done':
        return void (done.mutate({ task: a.item.task!, evidence: 'decided in Requests', reason: true }), read.mutate({ ids: [a.item.id] }));
      case 'done_task':
        return done.mutate({ task: a.task, evidence: 'Done', reason: true });
      case 'worktree':
        return void (done.mutate({ task: a.item.task!, evidence: WORKTREE_WORDS[a.answer], reason: true }),
        read.mutate({ ids: [a.item.id] }));
      case 'approve':
        return void (done.mutate({ task: a.item.task!, evidence: APPROVED, reason: true }), read.mutate({ ids: [a.item.id] }));
      case 'ask_change':
        return setAsking(true);
      case 'open':
        return card.task != null ? goTasks(card.task) : goHash(a.to);
      case 'terminal':
        return goTerminal(a.agent);
      case 'read':
        return read.mutate({ ids: [a.id] });
    }
  };
  // The buttons are the answers; a written answer is the field beside them, never a bare Reply. The rarer verbs are in the menu.
  const main = card.actions.slice(0, card.primary).filter((a) => a.type !== 'answer');
  const rest = card.actions.slice(card.primary).filter((a) => a.type !== 'answer');
  const answerId = card.actions.find((a) => a.type === 'answer');
  const askId = card.actions.find((a) => a.type === 'ask_change')?.item.id;
  const open =
    card.task != null
      ? () => goTasks(card.task!)
      : card.kind === 'machine'
        ? undefined
        : () => goHash(`#/agents/${encodeURIComponent(card.from ?? card.agent ?? '')}`);
  const source = (
    <Link onClick={open} className="h-5 gap-1 rounded-sm border border-line-strong px-1.5 text-xs">
      {sourceFull(card)}
      <ChevronRight width={12} height={12} strokeWidth={1.75} aria-hidden />
    </Link>
  );
  return (
    <article className="my-turn-card px-5 py-4" data-kind={card.kind} data-card={card.key}>
      <ItemHeader
        source={open ? source : <Chip>Machine</Chip>}
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
        kind={groupOf(card)}
        code={card.kind === 'approval' || card.kind === 'dialog'}
        ask={
          card.detail ? (
            <Fold note={card.detail} long={card.task != null && noteIsLong(card.detail)}>
              {(t) => <p className={`m-0${card.task != null ? '' : ' whitespace-pre-wrap'}`}>{t}</p>}
            </Fold>
          ) : null
        }
        full={
          rest.length ? (
            <MenuButton size="sm" icon="ellipsis" label="More">
              {rest.map((a, i) => (
                <MenuItem key={a.type + i} onSelect={() => run(a)}>
                  {label(a)}
                </MenuItem>
              ))}
            </MenuButton>
          ) : null
        }
      >
        {main.map((a, i) => (
          <DsButton key={a.type + i} kind={i === 0 ? 'primary' : 'secondary'} label={label(a)} onClick={() => run(a)} />
        ))}
      </Answer>
      {refused ? (
        <p className="m-0 mt-1 text-xs text-bad" role="alert">
          {sentence(classify(refused))}
        </p>
      ) : null}
      {answerId?.type === 'answer' && !card.review ? (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return;
            answer.mutate({ id: answerId.id, text: change ? changeAsked(text) : text.trim() });
            setChange(false);
            setText('');
          }}
        >
          <Input
            grow
            aria-label="Your answer"
            placeholder={change ? 'What to change' : 'Write an answer'}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <Button label="Send answer" type="submit" disabled={!text.trim()}>
            <Icon name="reply" aria-hidden /> Send
          </Button>
        </form>
      ) : null}
      {asking && card.task != null && askId ? <AskChange task={card.task} item={askId} onClose={() => setAsking(false)} /> : null}
    </article>
  );
}

export function RequestsPage() {
  const { cards, loading } = useMyTurn();
  const agents = useAgents();
  const wide = useWide(1100);
  const now = useNow();
  const [pick, setPick] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  // Wide: the first item is open, as the list is read top down. Narrow: the list alone until a row is picked.
  const sel = cards.find((c) => c.key === pick) ?? (wide ? cards[0] : undefined);
  const leads = (agents.data?.agents ?? []).filter((a) => !a.parent).map((a) => a.name);
  const sheet = asking ? (
    <NewTask request agents={leads.length ? leads : (agents.data?.agents ?? []).map((a) => a.name)} onClose={() => setAsking(false)} />
  ) : null;
  if (!wide && sel)
    return (
      <ScrollPage aria-label="Request">
        <PageHead back={{ label: 'Requests', to: () => setPick(null) }} />
        <CardDetail card={sel} />
      </ScrollPage>
    );
  const list = (
    <ScrollPage beside={wide} className="requests-page" aria-label="Requests">
      <PageHead>
        <PageTitle>
          Requests <span className="muted">{cards.length}</span>
        </PageTitle>
        <DsButton iconOnly icon={Plus} label="New request" size="s" onClick={() => setAsking(true)} />
      </PageHead>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : cards.length ? (
        GROUPS.map((g) => {
          const list = cards.filter((c) => groupOf(c) === g);
          return list.length ? (
            <section key={g} aria-label={g}>
              <SectionHead count={list.length}>{g}</SectionHead>
              {list.map((c) => (
                <div key={c.key} data-card-row={c.key}>
                  <ListRow
                    title={c.title}
                    chip={<Chip>{sourceWord(c)}</Chip>}
                    time={c.since ? ago(new Date(c.since).toISOString(), now) : undefined}
                    selected={sel?.key === c.key}
                    label={c.task != null ? `Open T${c.task}` : c.title}
                    onOpen={() => setPick(c.key)}
                  />
                </div>
              ))}
            </section>
          ) : null;
        })
      ) : (
        <div className="empty">
          <strong>Nothing needs you</strong>
          <p>Asks, reviews and tasks given to you show here.</p>
        </div>
      )}
    </ScrollPage>
  );
  if (!wide)
    return (
      <>
        {list}
        {sheet}
      </>
    );
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1">
      <div className="flex min-h-0 w-[min(420px,40%)] flex-none flex-col">{list}</div>
      <div className="min-h-0 min-w-0 flex-1 overflow-auto border-l border-line">{sel ? <CardDetail card={sel} /> : null}</div>
      {sheet}
    </div>
  );
}
