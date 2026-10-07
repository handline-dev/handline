// The Inbox page (#/inbox, #/inbox/:id): what waits on the person, newest first, filtered by kind and agent in the page, with the bridge's
// keys. The cards are InboxCard; the bell and the tab title read the same cached list (useUnread).
import { useEffect, useMemo, useState } from 'react';
import { useServes, useInbox, usePending, useUnread, useWrite } from '@handline/app-data';
import { useConnection } from '@handline/app-react';
import { InboxCard, kindLabel, originOf, type Verbs } from './InboxCard.tsx';
import { View } from './View.tsx';

const CONFIRM_ABOVE = 10;
const KEYS: [string, string][] = [
  ['↵', 'open'],
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
  const all = useMemo(() => list.data?.items ?? [], [list.data]);
  const shown = useMemo(() => all.filter((i) => (!kind || i.kind === kind) && (!agent || i.agent === agent)), [all, kind, agent]);
  const kinds = [...new Set(all.map((i) => i.kind))];
  const agents = [...new Set(all.flatMap((i) => (i.agent ? [i.agent] : [])))];
  const filtered = !!(kind || agent);
  // A deep link that names an item the list no longer holds (read and gone, dismissed) says so instead of showing nothing.
  const missing = item != null && list.isSuccess && !all.some((i) => i.id === item);

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
          const to = cur && originOf(cur);
          return void (to && (location.hash = to));
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
        <h2>
          Inbox<span className="muted"> · {unread.length} unread</span>
        </h2>
        <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {kindLabel(k)}
            </option>
          ))}
        </select>
        <select aria-label="Agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">All agents</option>
          {agents.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <button className="btn" disabled={!unread.length || !!verbs.off.read} title={verbs.off.read ?? undefined} onClick={askReadAll}>
          Mark all read
        </button>
        <button className="icon" aria-label="Keys" aria-expanded={sheet} onClick={() => setSheet(!sheet)}>
          ?
        </button>
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
          <button className="btn primary" autoFocus onClick={readAll}>
            Mark all read
          </button>{' '}
          <button className="btn" onClick={() => setConfirm(false)}>
            Cancel
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
        empty={!all.length && 'Nothing waits on you. Results, questions and blocks from your agents land here.'}
      >
        {shown.length ? (
          <div className="inbox-list">
            {shown.map((i) => (
              <InboxCard
                key={i.id}
                item={i}
                on={i.id === cursor}
                pending={pending.read.includes(i.id) || pending.answer.includes(i.id) || pending.reply.includes(i.id)}
                verbs={verbs}
              />
            ))}
          </div>
        ) : (
          <div className="empty">
            <strong>No items match</strong>
            {filtered ? (
              <p>
                <button
                  className="btn"
                  onClick={() => {
                    setKind('');
                    setAgent('');
                  }}
                >
                  Clear filters
                </button>
              </p>
            ) : null}
          </div>
        )}
      </View>
    </section>
  );
}
