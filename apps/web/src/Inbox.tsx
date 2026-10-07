// The Inbox page (#/inbox, #/inbox/:id): what waits on the person, newest first, filtered by kind and agent in the page, with the bridge's
// keys. The cards are InboxCard; the bell and the tab title read the same cached list (useUnread).
import { useEffect, useMemo, useState } from 'react';
import { useServes, useInbox, usePending, useUnread, useWrite } from '@handline/app-data';
import { useConnection } from '@handline/app-react';
import { AnimatedItem, AnimatedList } from './AnimatedList.tsx';
import { InboxCard, kindIcon, kindLabel, open, waits, type Verbs } from './InboxCard.tsx';
import { Button, Icon, Select } from './ui/index.ts';
import { View } from './View.tsx';

const CONFIRM_ABOVE = 10;
const KEYS: [string, string][] = [
  ['↵', 'open chat or task'],
  ['a', 'answer'],
  ['x', 'dismiss'],
  ['m', 'mark read'],
  ['M', 'mark all read'],
  ['j / k', 'next / previous'],
  ['?', 'this list'],
];
const typing = (t: EventTarget | null) => t instanceof HTMLElement && !!t.closest('input, textarea, select, [contenteditable]');

function useVerbs(): Verbs {
  const read = useWrite('inbox.mark_read');
  const dismiss = useWrite('chat.dismiss');
  const answer = useWrite('inbox.answer');
  const reply = useWrite('inbox.reply');
  const open = useConnection() === 'open';
  const served = {
    read: useServes('inbox.mark_read').served,
    dismiss: useServes('chat.dismiss').served,
    answer: useServes('inbox.answer').served,
    reply: useServes('inbox.reply').served,
  };
  const why = (verb: keyof typeof served, method: string) =>
    !open ? 'daemon not connected' : served[verb] ? null : `this daemon does not serve ${method}`;
  return {
    read: (id) => read.mutate({ ids: [id] }),
    dismiss: (id) => dismiss.mutate({ request: id }),
    answer: (id, how) => answer.mutate({ id, ...how }),
    reply: (id, text) => reply.mutate({ id, text }),
    off: {
      read: why('read', 'inbox.mark_read'),
      dismiss: why('dismiss', 'chat.dismiss'),
      answer: why('answer', 'inbox.answer'),
      reply: why('reply', 'inbox.reply'),
    },
  };
}

export function InboxPage({ item }: { item: number | null }) {
  const list = useInbox();
  const { served } = useServes('inbox.list');
  const unread = useUnread();
  const verbs = useVerbs();
  const markAll = useWrite('inbox.mark_read');
  const [kind, setKind] = useState('');
  const [agent, setAgent] = useState('');
  const [cursor, setCursor] = useState<number | null>(item);
  const [sheet, setSheet] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const pending = {
    read: usePending('inbox.mark_read').flatMap((p) => p.ids),
    answer: usePending('inbox.answer').map((p) => p.id),
    reply: usePending('inbox.reply').map((p) => p.id),
  };
  // Read items leave the list at once, those whose mark-read is still on its way too; a question waiting for its answer stays until answered.
  const all = useMemo(() => list.data?.items ?? [], [list.data]);
  const waiting = all.filter((i) => waits(i, pending.read.includes(i.id)));
  const shown = waiting.filter((i) => (!kind || i.kind === kind) && (!agent || i.agent === agent));
  const kinds = [...new Set(waiting.map((i) => i.kind))];
  const agents = [...new Set(waiting.flatMap((i) => (i.agent ? [i.agent] : [])))];
  const filtered = !!(kind || agent);
  // A deep link that names an item the list no longer holds (read and gone, dismissed) says so instead of showing nothing.
  const missing = item != null && list.isSuccess && !waiting.some((i) => i.id === item);

  useEffect(() => setCursor(item), [item]);
  useEffect(() => {
    if (item != null) document.querySelector(`[data-item="${item}"]`)?.scrollIntoView?.({ block: 'center' });
  }, [item, all.length]);

  const readAll = () => {
    setConfirm(false);
    if (unread.length) markAll.mutate({ ids: unread.map((i) => i.id) });
  };
  const askReadAll = () => (unread.length > CONFIRM_ABOVE ? setConfirm(true) : readAll());

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'Escape') return void (sheet ? setSheet(false) : confirm ? setConfirm(false) : setCursor(null));
      if (typing(e.target)) return;
      const at = shown.findIndex((i) => i.id === cursor);
      const cur = shown[at];
      const move = (to: number) => setCursor(shown[Math.min(shown.length - 1, Math.max(0, to))]?.id ?? null);
      switch (e.key) {
        case 'j':
        case 'ArrowDown':
          return move(at + 1);
        case 'k':
        case 'ArrowUp':
          return move(at < 0 ? 0 : at - 1);
        case 'Enter': {
          return void (cur && open(cur, verbs));
        }
        case 'a':
          return void document.querySelector<HTMLElement>(`[data-item="${cursor}"] button[data-verb]:not(:disabled)`)?.click();
        case 'x':
          return void (cur && !verbs.off.dismiss && verbs.dismiss(cur.id));
        case 'm':
          return void (cur && !cur.read && !verbs.off.read && verbs.read(cur.id));
        case 'M':
          return void (!verbs.off.read && askReadAll());
        case '?':
          return setSheet((s) => !s);
      }
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  });

  return (
    <section className="inbox" aria-label="Inbox">
      <header className="inbox-top">
        <h2 title="Inbox">
          <Icon name="inbox" size={20} />
          <span className="muted" aria-label={`${unread.length} unread`}>
            {' '}
            {unread.length}
          </span>
        </h2>
        <div className="inbox-kinds" role="group" aria-label="Kind">
          {kinds.map((k) => (
            <button
              key={k}
              className="icon"
              aria-pressed={kind === k}
              aria-label={kindLabel(k)}
              title={kindLabel(k)}
              onClick={() => setKind(kind === k ? '' : k)}
            >
              <Icon name={kindIcon(k)} label={kindLabel(k)} />
            </button>
          ))}
        </div>
        <Select
          label="Agent"
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
          options={[{ value: '', label: 'All agents' }, ...agents.map((a) => ({ value: a, label: a }))]}
        />
        <Button
          icon="markAllRead"
          label="Mark all read"
          disabled={!unread.length || !!verbs.off.read}
          title={verbs.off.read ?? 'Mark all read'}
          onClick={askReadAll}
        />
        <Button icon="keys" label="Keys" aria-expanded={sheet} onClick={() => setSheet(!sheet)} />
      </header>
      {sheet ? (
        <dl className="inbox-keys" aria-label="Keys of this page">
          {KEYS.map(([k, what]) => (
            <div key={k}>
              <dt>
                <kbd>{k}</kbd>
              </dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {confirm ? (
        <div className="inbox-confirm" role="alertdialog" aria-label="Mark all read">
          Mark all {unread.length} unread items read?{' '}
          <button className="icon" autoFocus aria-label="Mark all read" title="Mark all read" onClick={readAll}>
            <Icon name="markAllRead" />
          </button>{' '}
          <button className="icon" aria-label="Cancel" title="Cancel" onClick={() => setConfirm(false)}>
            <Icon name="close" label="Cancel" />
          </button>
        </div>
      ) : null}
      {missing ? (
        <p className="banner" role="status">
          Item {item} is no longer in the inbox. <a href="#/inbox">Show the inbox</a>
        </p>
      ) : null}
      <View
        q={list}
        served={served}
        shape="inbox"
        what="the inbox"
        empty={!waiting.length && 'Nothing waits on you. Results, questions and blocks from your agents land here.'}
      >
        {shown.length ? (
          <div className="inbox-list">
            <AnimatedList>
              {shown.map((i) => (
                <AnimatedItem key={i.id} layout={shown.length <= 60}>
                  <InboxCard
                    item={i}
                    on={i.id === cursor}
                    pending={pending.read.includes(i.id) || pending.answer.includes(i.id) || pending.reply.includes(i.id)}
                    verbs={verbs}
                  />
                </AnimatedItem>
              ))}
            </AnimatedList>
          </div>
        ) : (
          <div className="empty">
            <strong>No items match</strong>
            {filtered ? (
              <p>
                <button
                  className="icon"
                  aria-label="Clear filters"
                  title="Clear filters"
                  onClick={() => {
                    setKind('');
                    setAgent('');
                  }}
                >
                  <Icon name="close" label="Clear filters" />
                </button>
              </p>
            ) : null}
          </div>
        )}
      </View>
    </section>
  );
}
