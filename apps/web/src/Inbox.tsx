// The Inbox page (#/inbox, #/inbox/:id): what waits on the person, newest first, filtered by kind and agent in the page, with the bridge's
// keys. The cards are InboxCard; the bell and the tab title read the same cached list (useUnread).
import { useEffect, useRef, useState } from 'react';
import { useServes, useInboxPages, usePending, useUnread, useWrite } from '@handline/app-data';
import { inboxGroup, type InboxGroup } from '@handline/app-core';
import { useConnection, useNearEnd } from '@handline/app-react';
import { AnimatedItem, AnimatedList } from './AnimatedList.tsx';
import { InboxCard, open, waits } from './InboxCard.tsx';
import { kindIcon, kindLabel, type Verbs } from './inboxKinds.ts';
import { Button, Icon, MenuButton, MenuCheckItem, MenuLabel, MenuSeparator, PageHead, ScrollPage } from './ui/index.ts';
import { useScreenKeys, type KeyRows } from './screenKeys.ts';
import { View } from './View.tsx';

const CONFIRM_ABOVE = 10;
const KEYS: KeyRows = [
  ['↵', 'open chat or task'],
  ['a', 'reply'],
  ['m', 'mark read'],
  ['M', 'mark all read'],
  ['j / k', 'next / previous'],
];
// The header sticks.
const TOP =
  'inbox-top sticky top-0 z-1 flex flex-wrap items-center gap-2 bg-canvas py-3 ';
const GROUPS: [InboxGroup, string][] = [
  ['decisions', 'Decisions'],
  ['approvals', 'Approvals'],
  ['fyi', 'FYI'],
];
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
  const { query: list, items: loaded, more, loadOlder, loadingOlder } = useInboxPages();
  const page = useRef<HTMLElement>(null);
  const end = useNearEnd<HTMLElement, HTMLDivElement>(page, () => void loadOlder(), more && !loadingOlder);
  const { served } = useServes('inbox.list');
  const unread = useUnread();
  const verbs = useVerbs();
  const markAll = useWrite('inbox.mark_read');
  const [kind, setKind] = useState('');
  const [agent, setAgent] = useState('');
  const [cursor, setCursor] = useState<number | null>(item);
    const [confirm, setConfirm] = useState(false);
  useScreenKeys('In the inbox', KEYS);
  const pending = {
    read: usePending('inbox.mark_read').flatMap((p) => p.ids),
    decide: usePending('task.done').map((p) => ('task' in p ? p.task : 0)),
    reply: usePending('inbox.reply').map((p) => p.id),
  };
  // Read items leave the list at once, those whose mark-read is still on its way too.
  const all = loaded;
  const waiting = all.filter((i) => waits(i, pending.read.includes(i.id)));
  const matched = waiting.filter((i) => (!kind || i.kind === kind) && (!agent || i.agent === agent));
  const grouped = Object.fromEntries(GROUPS.map(([g]) => [g, matched.filter((i) => inboxGroup(i) === g)])) as Record<
    InboxGroup,
    typeof matched
  >;
  // FYI is folded until asked for, but a deep link into it opens it.
  const [fyiOpen, setFyiOpen] = useState(false);
  const [fyiClosed, setFyiClosed] = useState(false); // the header closed it while a deep link held it open
  const fyiShown = fyiOpen || (!fyiClosed && item != null && grouped.fyi.some((i) => i.id === item));
  // Mark all FYI read ignores the kind and agent filters: it is all of the FYI group.
  const unreadFyi = waiting.filter((i) => inboxGroup(i) === 'fyi' && !i.read);
  const shown = [...grouped.decisions, ...grouped.approvals, ...(fyiShown ? grouped.fyi : [])];
  const kinds = [...new Set(waiting.map((i) => i.kind))];
  const agents = [...new Set(waiting.flatMap((i) => (i.agent ? [i.agent] : [])))];
  const filtered = !!(kind || agent);
  // A deep link that names an item the list no longer holds (read and gone) says so instead of showing nothing.
  const missing = item != null && list.isSuccess && !waiting.some((i) => i.id === item);

  useEffect(() => {
    setCursor(item);
    setFyiClosed(false);
  }, [item]);
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
      if (e.key === 'Escape') return void (confirm ? setConfirm(false) : setCursor(null));
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
      }
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  });

  return (
    <ScrollPage ref={page} className="inbox" aria-label="Inbox">
      <PageHead className={TOP}>
        <h2 className="m-0 mr-auto text-[18px] [&_svg]:align-middle" title="Inbox">
          <Icon name="inbox" size={20} />
          <span className="muted" aria-label={`${unread.length} unread`}>
            {' '}
            {unread.length}
          </span>
        </h2>
        <MenuButton
          icon="filter"
          label={filtered ? 'Filter: on' : 'Filter'}
          name="Show items that are"
          className={filtered ? 'text-primary' : ''}
        >
          {kinds.length ? <MenuLabel>Kind</MenuLabel> : null}
          {kinds.map((k) => (
            <MenuCheckItem key={k} checked={kind === k} icon={<Icon name={kindIcon(k)} size={14} aria-hidden />} onCheckedChange={() => setKind(kind === k ? '' : k)}>
              {kindLabel(k)}
            </MenuCheckItem>
          ))}
          {kinds.length && agents.length ? <MenuSeparator /> : null}
          {agents.length ? <MenuLabel>Agent</MenuLabel> : null}
          {agents.map((a) => (
            <MenuCheckItem key={a} checked={agent === a} onCheckedChange={() => setAgent(agent === a ? '' : a)}>
              {a}
            </MenuCheckItem>
          ))}
        </MenuButton>
        <Button
          icon="markAllRead"
          label="Mark all read"
          disabled={!unread.length || !!verbs.off.read}
          title={verbs.off.read ?? 'Mark all read'}
          onClick={askReadAll}
        />
      </PageHead>
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
        {matched.length ? (
          <div className="inbox-list flex flex-col gap-3">
            {GROUPS.map(([g, label]) => {
              const rows = grouped[g];
              if (!rows.length) return null;
              const fyi = g === 'fyi';
              return (
                <section key={g} className={`inbox-group inbox-${g} flex flex-col gap-1`} aria-label={label}>
                  <h3 className="m-0 flex items-center gap-2 text-[13px] text-ink-2">
                    {fyi ? (
                      <button
                        className="flex items-center gap-1 border-0 bg-transparent p-0 text-inherit"
                        aria-expanded={fyiShown}
                        onClick={() => (setFyiOpen(!fyiShown), setFyiClosed(fyiShown))}
                      >
                        <Icon name={fyiShown ? 'chevron' : 'chevronRight'} size={14} aria-hidden />
                        {label}
                      </button>
                    ) : (
                      label
                    )}
                    <span className="muted" aria-label={`${rows.length} items`}>
                      {rows.length}
                    </span>
                    {fyi ? (
                      <Button
                        icon="markAllRead"
                        label="Mark all FYI read"
                        className="ml-auto"
                        disabled={!unreadFyi.length || !!verbs.off.read}
                        title={verbs.off.read ?? (filtered ? 'Mark all FYI read, filters ignored' : 'Mark all FYI read')}
                        onClick={() => markAll.mutate({ ids: unreadFyi.map((i) => i.id) })}
                      />
                    ) : null}
                  </h3>
                  {fyi && !fyiShown ? null : (
                    <AnimatedList>
                      {rows.map((i) => (
                        <AnimatedItem key={i.id} layout={rows.length <= 60}>
                          <InboxCard
                            item={i}
                            on={i.id === cursor}
                            pending={
                              pending.read.includes(i.id) ||
                              (i.task != null && pending.decide.includes(i.task)) ||
                              pending.reply.includes(i.id)
                            }
                            verbs={verbs}
                          />
                        </AnimatedItem>
                      ))}
                    </AnimatedList>
                  )}
                </section>
              );
            })}
            {more ? <div ref={end} aria-hidden className="h-px" /> : null}
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
    </ScrollPage>
  );
}
