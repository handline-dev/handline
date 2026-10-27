// The notices (T5591 N2): the bell at the status bar's right end with the unread count, the list that opens upward from it (a link to what
// needs the person, then what happened, newest first), and the short results stacked above it.
import { useEffect, useState } from 'react';
import { targetOf, type Notice } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import { useMyTurn } from '#src/MyTurn.tsx';
import { Icon, Popup } from '#src/ui/index.ts';

const lineOf = (n: Notice) => (n.count > 1 ? `${n.text} ×${n.count}` : n.text);

export function NoticeBell() {
  const store = useStore();
  const unread = useSelector((s) => s.notices.unread());
  const list = useSelector((s) => s.notices.list());
  const waiting = useMyTurn().cards.length;
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <Toasts />
      <button
        type="button"
        className="notice-bell inline-flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-ink-2"
        aria-label={unread ? `Notices, ${unread} unread` : 'Notices'}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="alert" size={14} aria-hidden />
        {unread ? <span className={open ? '' : 'font-bold text-ink'}>{unread}</span> : null}
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
              <span className="flex-1">
                {waiting} {waiting === 1 ? 'needs' : 'need'} you
              </span>
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

const hueOf = (name: string) => [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
/** The two letters after the team prefix: `handline.pc.auditor` is `au`, `handline.pc` is `pc`. */
const markOf = (agent: string) => (agent.split('.').at(-1) ?? agent).slice(0, 2).toLowerCase();

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
  const sub = about?.task != null ? [`T${about.task} done`, about.result].filter(Boolean).join(' · ') : null;
  const go = targetOf(n) !== null;
  return (
    <button
      type="button"
      className={`notice-row flex w-full items-center gap-2 border-0 bg-transparent px-3 py-1.5 text-left text-body text-ink ${go ? 'cursor-pointer hover:bg-panel' : 'cursor-default'}`}
      data-notice
      onClick={() => (openNotice(store, n), onOpened())}
    >
      {about?.agent ? (
        <span
          className="grid size-4 flex-none place-items-center rounded-[4px] text-[10px] font-semibold text-white"
          style={{ background: `hsl(${hueOf(about.agent)} 45% 45%)` }}
          title={about.agent}
          aria-label={about.agent}
        >
          {markOf(about.agent)}
        </span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className={`block truncate ${n.level === 'fail' ? 'text-bad' : ''} ${n.read ? '' : 'font-semibold'}`}>{head}</span>
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
function Toasts() {
  const store = useStore();
  const toasts = useSelector((s) => s.notices.toasts());
  useAway((away) => store.notices.setAway(away));
  if (!toasts.length) return null;
  return (
    <div className="toasts absolute right-0 bottom-full z-20 mb-2 flex w-[360px] max-w-[90vw] flex-col-reverse gap-1.5" role="status">
      {toasts.map((n) => {
        const go = targetOf(n) !== null;
        return (
          <div
            key={n.id}
            className={`toast relative flex items-start gap-2 rounded-[8px] px-3 py-2 text-body shadow-[var(--shadow-2)] ${n.level === 'fail' ? 'bg-bad text-canvas' : 'bg-ink text-canvas'}`}
            onPointerEnter={() => store.notices.hold(n.id, 'pointer')}
            onPointerLeave={() => store.notices.release(n.id, 'pointer')}
            onFocus={() => store.notices.hold(n.id, 'focus')}
            onBlur={() => store.notices.release(n.id, 'focus')}
          >
            <div className="min-w-0 flex-1">
              {go ? (
                <button
                  type="button"
                  className="toast-open cursor-pointer border-0 bg-transparent p-0 text-left text-inherit"
                  onClick={() => openNotice(store, n)}
                >
                  {lineOf(n)}
                </button>
              ) : (
                <span>{lineOf(n)}</span>
              )}
              {n.retry ? (
                <button
                  type="button"
                  className="toast-retry ml-2 cursor-pointer border-0 bg-transparent p-0 text-hint text-inherit underline"
                  onClick={() => (n.retry?.(), store.notices.close(n.id))}
                >
                  Try again
                </button>
              ) : null}
              {n.details ? (
                <details className="mt-1 text-hint">
                  <summary className="cursor-pointer">Details</summary>
                  {n.details}
                </details>
              ) : null}
            </div>
            <button
              type="button"
              className="toast-close flex-none cursor-pointer border-0 bg-transparent p-0 text-inherit opacity-70 hover:opacity-100"
              aria-label="Close"
              onClick={() => store.notices.close(n.id)}
            >
              <Icon name="close" size={12} aria-hidden />
            </button>
          </div>
        );
      })}
    </div>
  );
}
