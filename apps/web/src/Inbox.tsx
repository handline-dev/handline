// The Inbox page (#/inbox, #/inbox/:id): what waits on the person, newest first, filtered by kind and agent in the page, with the bridge's
// keys. The cards are InboxCard; the bell and the tab title read the same cached list (useUnread).
import { useEffect, useMemo, useState } from 'react';
import { useServes, useInbox, usePending, useUnread, useWrite } from '@handline/app-data';
import { useConnection } from '@handline/app-react';
import { AnimatedItem, AnimatedList } from './AnimatedList.tsx';
import { InboxCard, open, waits } from './InboxCard.tsx';
import { kindIcon, kindLabel, type Verbs } from './inboxKinds.ts';
import { Button, Icon, Kbd, Select } from './ui/index.ts';
import { View } from './View.tsx';

const CONFIRM_ABOVE = 10;
const KEYS: [string, string][] = [
  ['↵', 'open chat or task'],
  ['a', 'reply'],
  ['m', 'mark read'],
  ['M', 'mark all read'],
  ['j / k', 'next / previous'],
  ['?', 'this list'],
];
// The header sticks; its agent picker takes the page's frame over the Select's own.
const TOP =
  'inbox-top sticky top-0 z-1 flex flex-wrap items-center gap-2 bg-canvas py-3 [&_select]:rounded-ctl [&_select]:border [&_select]:border-line [&_select]:bg-panel [&_select]:px-1.5 [&_select]:py-[5px] [&_select]:text-ink';
// A pressed filter is shown by a frame and a heavier stroke, not by colour.
const KIND = 'icon aria-pressed:outline-2 aria-pressed:outline-offset-1 aria-pressed:outline-current aria-pressed:[&_svg]:stroke-3';
const typing = (t: EventTarget | null) => t instanceof HTMLElement && !!t.closest('input, textarea, select, [contenteditable]');

function useVerbs(): Verbs {
  const read = useWrite('inbox.mark_read');
  const done = useWrite('task.done');
  const reply = useWrite('inbox.reply');
  const open = useConnection() === 'open';
  const served = {
    read: useServes('inbox.mark_read').served,
    decide: useServes('task.done').served,
    reply: useServes('inbox.reply').served,
  };
  const why = (verb: keyof typeof served, method: string) =>
    !open ? 'daemon not connected' : served[verb] ? null : `this daemon does not serve ${method}`;
  return {
    read: (id) => read.mutate({ ids: [id] }),
    decide: (i) => (done.mutate({ task: i.task!, evidence: 'decided in the Inbox', reason: true }), read.mutate({ ids: [i.id] })),
    reply: (id, text) => reply.mutate({ id, text }),
    off: {
      read: why('read', 'inbox.mark_read'),
      decide: why('decide', 'task.done'),
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
    decide: usePending('task.done').map((p) => ('task' in p ? p.task : 0)),
    reply: usePending('inbox.reply').map((p) => p.id),
  };
  // Read items leave the list at once, those whose mark-read is still on its way too.
  const all = useMemo(() => list.data?.items ?? [], [list.data]);
  const waiting = all.filter((i) => waits(i, pending.read.includes(i.id)));
  const shown = waiting.filter((i) => (!kind || i.kind === kind) && (!agent || i.agent === agent));
  const kinds = [...new Set(waiting.map((i) => i.kind))];
  const agents = [...new Set(waiting.flatMap((i) => (i.agent ? [i.agent] : [])))];
  const filtered = !!(kind || agent);
  // A deep link that names an item the list no longer holds (read and gone) says so instead of showing nothing.
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
    <section className="inbox mx-auto h-full max-w-[760px] overflow-auto px-4 pb-6" aria-label="Inbox">
      <header className={TOP}>
        <h2 className="m-0 mr-auto text-[18px] [&_svg]:align-middle" title="Inbox">
          <Icon name="inbox" size={20} />
          <span className="muted" aria-label={`${unread.length} unread`}>
            {' '}
            {unread.length}
          </span>
        </h2>
        <div className="inbox-kinds flex gap-1" role="group" aria-label="Kind">
          {kinds.map((k) => (
            <button
              key={k}
              className={KIND}
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
        <dl
          className="inbox-keys m-0 mb-2 flex flex-wrap gap-x-4 gap-y-1 rounded-ctl border border-line bg-panel px-2.5 py-2"
          aria-label="Keys of this page"
        >
          {KEYS.map(([k, what]) => (
            <div key={k} className="flex gap-1.5">
              <dt className="m-0">
                <Kbd>{k}</Kbd>
              </dt>
              <dd className="m-0 text-ink-2">{what}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {confirm ? (
        <div
          className="inbox-confirm m-0 mb-2 rounded-ctl border border-line bg-panel px-2.5 py-2"
          role="alertdialog"
          aria-label="Mark all read"
        >
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
          <div className="inbox-list flex flex-col gap-1">
            <AnimatedList>
              {shown.map((i) => (
                <AnimatedItem key={i.id} layout={shown.length <= 60}>
                  <InboxCard
                    item={i}
                    on={i.id === cursor}
                    pending={
                      pending.read.includes(i.id) || (i.task != null && pending.decide.includes(i.task)) || pending.reply.includes(i.id)
                    }
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
