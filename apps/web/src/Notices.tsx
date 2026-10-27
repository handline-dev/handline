// The notices (T5591 N2): the bell at the status bar's right end with the unread count, the list that opens upward from it (a link to what
// needs the person, then what happened, newest first), and the short results stacked above it.
import { useEffect, useState } from 'react';
import { targetOf, type Notice } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import { useMyTurn } from '@handline/app-data';
import { Button, Icon, Popup } from '#src/ui/index.ts';

const lineOf = (n: Notice) => (n.count > 1 ? `${n.text} ×${n.count}` : n.text);

/** What the main button of a notice says it opens. */
function openWord(n: Notice): string {
  const to = targetOf(n) ?? '';
  return to.startsWith('#/tasks/')
    ? 'Open task'
    : to.startsWith('#/accounts/')
      ? 'Open account'
      : to.startsWith('#/team/')
        ? 'Open proposal'
        : 'Open chat';
}
const firstLine = (text: string) => text.split('\n')[0]!.trim();
/** The short second line under a notice's title: what to do for what needs the person, the daemon's own first line for a failure, the agent for a finished task. */
function secondLine(n: Notice): string | null {
  if (n.level === 'fail') return n.details ? firstLine(n.details) : null;
  if (n.level === 'need') return n.about?.account ? 'Open the account to see why.' : 'Open its chat to see why.';
  return n.about?.agent ?? null;
}

export function NoticeBell() {
  const store = useStore();
  const unread = useSelector((s) => s.notices.unread());
  const list = useSelector((s) => s.notices.list());
  const waiting = useMyTurn().cards.length;
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <Toasts hidden={open} />
      <button
        type="button"
        className="notice-bell tint -mx-1 inline-flex cursor-pointer items-center gap-1 rounded-chip border-0 bg-transparent px-1 py-0 text-ink-2"
        aria-label={unread ? `Notices, ${unread} unread` : 'Notices'}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="alert" size={14} aria-hidden />
        {unread ? <span className="size-1.5 rounded-full bg-wait" aria-hidden /> : null}
      </button>
      <Popup open={open} onClose={() => setOpen(false)} title="Notices" align="right" up bare>
        <div className="max-h-[60vh] w-[360px] max-w-[90vw] overflow-y-auto">
          {waiting ? (
            <button
              type="button"
              className="notice-turn flex w-full cursor-pointer items-center gap-2 border-0 border-b border-line bg-[color-mix(in_srgb,var(--color-wait)_14%,transparent)] px-3 py-1.5 text-left text-body font-medium text-ink"
              onClick={() => void ((location.hash = '#/my-turn'), setOpen(false))}
            >
              <span className="size-2 flex-none rounded-full bg-wait" aria-hidden />
              <span className="flex-1">My turn</span>
              <Icon name="chevronRight" size={14} label="Opens My turn" aria-hidden />
            </button>
          ) : null}
          {list.length === 0 && !waiting ? <p className="m-0 px-3 py-2 text-hint text-ink-3">Nothing new.</p> : null}
          {list.map((n) => (
            <Row key={n.id} n={n} onOpened={() => setOpen(false)} />
          ))}
        </div>
        {list.length ? (
          <footer className="flex items-center justify-between border-t border-line px-3 py-1.5 text-hint text-ink-3">
            <span>{unread ? `${unread} unread` : 'All read'}</span>
            <button
              type="button"
              className="cursor-pointer border-0 bg-transparent p-0 text-hint text-ink-2 disabled:opacity-50"
              disabled={!unread}
              onClick={() => store.notices.markAllRead()}
            >
              Mark all read
            </button>
          </footer>
        ) : null}
      </Popup>
    </span>
  );
}

/** The page a notice points to opens and the notice is read; one that points nowhere only reads. */
function openNotice(store: ReturnType<typeof useStore>, n: Notice) {
  const to = targetOf(n);
  store.notices.open(n.id);
  if (to) location.hash = to;
}

/** One notice: who did it, what it is about (the task's title), the outcome in a line, the time. Unread ones are bold until opened; a click opens what it is about. */
function Row({ n, onOpened }: { n: Notice; onOpened: () => void }) {
  const store = useStore();
  const { about } = n;
  const head = about?.title ?? lineOf(n);
  const sub =
    n.level === 'fail'
      ? n.details
        ? firstLine(n.details)
        : null
      : about?.task != null
        ? [about.agent, `T${about.task} done`, about.result].filter(Boolean).join(' · ')
        : null;
  const [icon, tone] = MARK[n.level];
  const go = targetOf(n) !== null;
  return (
    <button
      type="button"
      className={`notice-row flex w-full items-center gap-2 border-0 bg-transparent px-3 py-1.5 text-left text-body text-ink ${go ? 'cursor-pointer hover:bg-panel' : 'cursor-default'}`}
      data-notice
      onClick={() => (openNotice(store, n), onOpened())}
    >
      <span className={`flex flex-none ${tone}`}>
        <Icon name={icon} size={16} aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className={`block truncate ${n.read ? '' : 'font-semibold'}`}>{head}</span>
        {sub ? <span className="block truncate text-hint text-ink-3">{sub}</span> : null}
      </span>
      <time className="flex-none text-hint text-ink-3">
        {new Date(n.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </time>
    </button>
  );
}

/** True while the window is shown and has the focus: no toast's clock runs otherwise, so nothing goes by unseen. */
function useAway(onChange: (away: boolean) => void) {
  useEffect(() => {
    const tell = () => onChange(document.visibilityState === 'hidden' || !document.hasFocus());
    tell();
    document.addEventListener('visibilitychange', tell);
    addEventListener('focus', tell);
    addEventListener('blur', tell);
    return () => {
      document.removeEventListener('visibilitychange', tell);
      removeEventListener('focus', tell);
      removeEventListener('blur', tell);
    };
  }, [onChange]);
}

/**
 * The notices above the bell. News goes by itself after 6 s; what needs the person and a failure stay until opened or closed. A pointer over a
 * toast or the keyboard focus in it stops its clock, as does a hidden or unfocused window. A click opens what it is about and closes only it.
 */
const MARK = { need: ['warn', 'text-wait'], fail: ['failed', 'text-bad'], ok: ['done', 'text-ok'], info: ['info', 'text-ink-3'] } as const;
const CHIP = 'bg-raised! hover:enabled:brightness-95';

/** One toast: a normal card on the neutral panel, the status icon, a bold title and a short second line, then its buttons: the main one filled, the rest grey chips. */
function Toast({ n }: { n: Notice }) {
  const store = useStore();
  const [more, setMore] = useState(false);
  const [icon, tone] = MARK[n.level];
  const go = targetOf(n) !== null;
  const sub = secondLine(n);
  const long = !!n.details;
  return (
    <div
      className="toast relative flex items-start gap-2 rounded-card border border-line bg-panel px-3 py-2 text-body text-ink shadow-[var(--shadow-2)]"
      onPointerEnter={() => store.notices.hold(n.id, 'pointer')}
      onPointerLeave={() => store.notices.release(n.id, 'pointer')}
      onFocus={() => store.notices.hold(n.id, 'focus')}
      onBlur={() => store.notices.release(n.id, 'focus')}
    >
      <span className={`mt-0.5 flex flex-none ${tone}`}>
        <Icon name={icon} size={16} aria-hidden />
      </span>
      <div className="grid min-w-0 flex-1 gap-1">
        <span className="font-semibold">{lineOf(n)}</span>
        {sub ? <span className="text-ink-2">{sub}</span> : null}
        {more && n.details ? <span className="whitespace-pre-wrap text-hint text-ink-3">{n.details}</span> : null}
        {go || n.retry || long ? (
          <span className="mt-0.5 flex flex-wrap gap-1.5">
            {go ? (
              <Button variant="solid" size="sm" label={openWord(n)} className="toast-open" onClick={() => openNotice(store, n)}>
                {openWord(n)}
              </Button>
            ) : null}
            {n.retry ? (
              <Button
                variant="outline"
                size="sm"
                icon="retry"
                label="Try again"
                className={`toast-retry ${CHIP}`}
                onClick={() => (n.retry?.(), store.notices.close(n.id))}
              >
                Try again
              </Button>
            ) : null}
            {long ? (
              <Button variant="outline" size="sm" label="Details" className={CHIP} aria-expanded={more} onClick={() => setMore(!more)}>
                Details
              </Button>
            ) : null}
          </span>
        ) : null}
      </div>
      <button
        type="button"
        className="toast-close flex-none cursor-pointer border-0 bg-transparent p-0 text-ink-3 hover:text-ink"
        aria-label="Close"
        onClick={() => store.notices.close(n.id)}
      >
        <Icon name="close" size={12} aria-hidden />
      </button>
    </div>
  );
}

function Toasts({ hidden }: { hidden: boolean }) {
  const store = useStore();
  const toasts = useSelector((s) => s.notices.toasts());
  useAway((away) => store.notices.setAway(away));
  // Escape closes the toast the focus is in, else the newest one.
  const first = toasts[0]?.id;
  useEffect(() => {
    if (first === undefined || hidden) return;
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      const held = document.activeElement?.closest('.toast');
      const at = held ? [...document.querySelectorAll('.toasts .toast')].indexOf(held) : -1;
      store.notices.close(store.notices.toasts()[at >= 0 ? at : 0]!.id);
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  }, [first, hidden, store]);
  if (!toasts.length || hidden) return null;
  return (
    <div className="toasts absolute right-0 bottom-full z-20 mb-2 flex w-[360px] max-w-[90vw] flex-col-reverse gap-1.5" role="status">
      {toasts.map((n) => (
        <Toast key={n.id} n={n} />
      ))}
    </div>
  );
}
