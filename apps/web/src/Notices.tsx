// The notices (T5591 N2): the bell at the status bar's right end with the unread count, the list that opens upward from it (a link to what
// needs the person, then the team's and the system's), and the short results stacked above it.
import { useState } from 'react';
import type { Notice } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import { useMyTurn } from '#src/MyTurn.tsx';
import { Icon, Popup, PROW, PopupRow } from '#src/ui/index.ts';

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
  const lane = (l: Notice['lane']) => list.filter((n) => n.lane === l);
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
      <Popup open={open} onClose={() => setOpen(false)} title="Notices" align="right" up>
        <div className="max-h-[60vh] w-[360px] max-w-[90vw] overflow-y-auto">
          <PopupRow
            icon="needs"
            label={waiting ? `${waiting} waiting on you` : 'Nothing waiting on you'}
            opens
            onClick={() => void ((location.hash = '#/my-turn'), setOpen(false))}
          />
          <Lane title="Team" items={lane('team')} fresh={fresh} />
          <Lane title="System" items={lane('system')} fresh={fresh} />
        </div>
        <footer className="border-t border-line">
          <button type="button" className={PROW} disabled={!unread} onClick={() => store.notices.markAllRead()}>
            Mark all read
          </button>
        </footer>
      </Popup>
    </span>
  );
}

function Lane({ title, items, fresh }: { title: string; items: Notice[]; fresh: Set<number> }) {
  return (
    <section aria-label={title}>
      <h3 className="px-2 pt-2 pb-1 text-hint font-medium text-ink-3">{title}</h3>
      {items.length === 0 ? <p className="px-2 pb-1 text-hint text-ink-3">Nothing yet.</p> : null}
      {items.map((n) => (
        <div key={n.id} className="flex items-baseline gap-2 px-2 py-1 text-body" data-notice>
          <span className={`flex-1 ${n.level === 'fail' ? 'text-bad' : ''} ${fresh.has(n.id) ? 'font-bold' : ''}`}>{lineOf(n)}</span>
          <time className="text-hint text-ink-3">{new Date(n.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
        </div>
      ))}
    </section>
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
