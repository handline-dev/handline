// The notices: the bell at the status bar's right end with the unread count (a click opens the Inbox and counts them read), and the short
// results stacked above it.
import type { Notice } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import { Icon } from '#src/ui/index.ts';

const lineOf = (n: Notice) => (n.count > 1 ? `${n.text} ×${n.count}` : n.text);

export function NoticeBell() {
  const store = useStore();
  const unread = useSelector((s) => s.notices.unread());
  return (
    <span className="relative inline-flex">
      <Toasts />
      <button
        type="button"
        className="notice-bell inline-flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-ink-2"
        aria-label={unread ? `Notices, ${unread} unread; open the Inbox` : 'Notices; open the Inbox'}
        title="Open the Inbox"
        onClick={() => {
          store.notices.markAllRead();
          location.hash = '#/my-turn';
        }}
      >
        <Icon name="alert" size={14} aria-hidden />
        {unread ? <span className="font-bold text-ink">{unread}</span> : null}
      </button>
    </span>
  );
}

/** The results of what was just done, above the bell: a click closes them, a pointer over one keeps it, each runs out by itself. */
function Toasts() {
  const store = useStore();
  // The store speaks again when a toast runs out, so reading them once per store version keeps the list current.
  const toasts = useSelector((s) => s.notices.toasts());
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
