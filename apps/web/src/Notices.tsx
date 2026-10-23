// The notices (T5591 N2): the bell at the status bar's right end with the unread count, the list that opens upward from it (a link to what
// needs the person, then what happened, newest first), and the short results stacked above it.
import { useState } from 'react';
import type { Notice } from '@handline/app-core';
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
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const show = () => {
    setFresh(new Set(list.filter((n) => !n.read).map((n) => n.id)));
    setOpen(true);
  };
  return (
    <span className="relative inline-flex">
      <Toasts />
      <button
        type="button"
        className="notice-bell inline-flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-ink-2"
        aria-label={unread ? `Notices, ${unread} unread` : 'Notices'}
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : show())}
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
              <span className="flex-1">{waiting} waiting on you</span>
              <Icon name="chevronRight" size={14} label="Opens My turn" aria-hidden />
            </button>
          ) : null}
          {list.length === 0 && !waiting ? <p className="m-0 px-3 py-2 text-hint text-ink-3">Nothing new.</p> : null}
          {list.map((n) => (
            <Row key={n.id} n={n} fresh={fresh.has(n.id)} />
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

/** One notice: who did it, what it is about (the task's title), the outcome in a line, the time. A notice without a task shows its words alone. */
function Row({ n, fresh }: { n: Notice; fresh: boolean }) {
  const { about } = n;
  const head = about?.title ?? lineOf(n);
  const sub = about ? [about.task != null ? `T${about.task} done` : 'done', about.result].filter(Boolean).join(' · ') : null;
  return (
    <div className="notice-row flex items-center gap-2 px-3 py-1.5 text-body" data-notice>
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
        <span className={`block truncate ${n.level === 'fail' ? 'text-bad' : ''} ${fresh ? 'font-semibold' : ''}`}>{head}</span>
        {sub ? <span className="block truncate text-hint text-ink-3">{sub}</span> : null}
      </span>
      <time className="flex-none text-hint text-ink-3">
        {new Date(n.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </time>
    </div>
  );
}

/** The results of what was just done, above the bell: a click closes them, a pointer over one keeps it, each runs out by itself. */
function Toasts() {
  const store = useStore();
  useSelector((s) => s.version); // the store speaks again when a toast runs out; what is on screen depends on the clock, so it is read here and not cached
  const toasts = store.notices.toasts();
  if (!toasts.length) return null;
  return (
    <div className="toasts absolute right-0 bottom-full z-20 mb-2 flex w-[360px] max-w-[90vw] flex-col-reverse gap-1.5" role="status">
      {toasts.map((n) => (
        <div
          key={n.id}
          className={`toast cursor-pointer rounded-[8px] px-3 py-2 text-body shadow-[var(--shadow-2)] ${n.level === 'fail' ? 'bg-bad text-canvas' : 'bg-ink text-canvas'}`}
          title="Click to dismiss"
          onClick={() => store.notices.dismissToasts()}
          onPointerEnter={() => store.notices.hold(n.id)}
          onPointerLeave={() => store.notices.release(n.id)}
        >
          {lineOf(n)}
        </div>
      ))}
    </div>
  );
}
