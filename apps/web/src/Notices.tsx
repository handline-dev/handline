// The notices: the bell at the status bar's right end with the unread count, the list that opens upward from it (what happened, newest first), and the short results stacked above it.
import { UpdateNotice } from '#src/UpdateNotice.tsx';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { clockTime, SPARE_SHOWN, spareNotice, targetOf, type Notice, type SpareNotice } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import { useTasks, useWrite } from '@handline/app-data';
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
  const spare = useSpareNotices();
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <UpdateNotice />
      <Toasts hidden={open} />
      <button
        type="button"
        className="notice-bell tint -mx-1 inline-flex cursor-pointer items-center gap-1 rounded-sm border-0 bg-transparent px-1 py-0 text-ink-2"
        aria-label={unread ? `Notices, ${unread} unread` : 'Notices'}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="alert" size={14} aria-hidden />
        {unread ? <span className="size-1.5 rounded-full bg-wait" aria-hidden /> : null}
      </button>
      <Popup open={open} onClose={() => setOpen(false)} title="Notices" align="right" up bare>
        <div className="max-h-[60vh] w-[360px] max-w-[90vw] overflow-y-auto">
          {spare.map((n) => (
            <div key={n.task} className="border-b border-line">
              <SpareCard n={n} />
            </div>
          ))}
          {list.length === 0 && !spare.length ? <p className="m-0 px-3 py-2 text-xs text-ink-3">Nothing new.</p> : null}
          {list.map((n) => (
            <Row key={n.id} n={n} onOpened={() => setOpen(false)} />
          ))}
        </div>
        {list.length ? (
          <footer className="flex items-center justify-between border-t border-line px-3 py-1.5 text-xs text-ink-3">
            <span>{unread ? `${unread} unread` : 'All read'}</span>
            <span className="flex gap-3">
              <button
                type="button"
                className="cursor-pointer border-0 bg-transparent p-0 text-xs text-ink-2 disabled:opacity-50"
                disabled={!unread}
                onClick={() => store.notices.markAllRead()}
              >
                Mark all read
              </button>
              <button
                type="button"
                className="cursor-pointer border-0 bg-transparent p-0 text-xs text-ink-2 disabled:opacity-50"
                disabled={list.every((n) => !n.read)}
                onClick={() => store.notices.removeRead()}
              >
                Remove read ones
              </button>
            </span>
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
  const [icon, level] = MARK[n.level];
  const tone = n.live?.mark === 'restart' ? 'text-acc-ink' : level;
  const go = targetOf(n) !== null;
  if (n.live)
    return (
      <div className="notice-row flex items-center gap-2 px-3 py-1.5 text-md text-ink" data-notice>
        <span className={`flex flex-none ${tone}`}>
          <Icon name={n.live.mark === 'restart' ? 'retry' : icon} size={16} aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className={`block truncate ${n.read ? '' : 'font-semibold'}`}>{head}</span>
          {n.live.step ? <span className="block truncate text-xs text-ink-3">{n.live.step}</span> : null}
        </span>
        <Actions n={n} />
        <time className="flex-none text-xs text-ink-3">{clockTime(new Date(n.at).toISOString())}</time>
      </div>
    );
  return (
    <button
      type="button"
      className={`notice-row flex w-full items-center gap-2 border-0 bg-transparent px-3 py-1.5 text-left text-md text-ink ${go ? 'cursor-pointer tint' : 'cursor-default'}`}
      data-notice
      onClick={() => (openNotice(store, n), onOpened())}
    >
      <span className={`flex flex-none ${tone}`}>
        <Icon name={icon} size={16} aria-hidden />
      </span>
      <span className="min-w-0 flex-1">
        <span className={`block truncate ${n.read ? '' : 'font-semibold'}`}>{head}</span>
        {sub ? <span className="block truncate text-xs text-ink-3">{sub}</span> : null}
      </span>
      <time className="flex-none text-xs text-ink-3">{clockTime(new Date(n.at).toISOString())}</time>
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
/** The stack shows the newest three; the bell lists the rest. */
const MAX_TOASTS = 3;
const SPARE_STAYS_MS = 10_000;
const CHIP = 'bg-raised! tint';

/** The buttons of a kept notice: the main one filled, the rest grey chips. The notice is read once one is pressed. */
function Actions({ n }: { n: Notice }) {
  const store = useStore();
  return (
    <span className="mt-0.5 flex flex-wrap gap-1.5">
      {n.live?.actions?.map((a) => (
        <Button
          key={a.label}
          variant={a.primary ? 'solid' : 'outline'}
          size="sm"
          label={a.label}
          className={a.primary ? 'toast-open' : CHIP}
          onClick={() => {
            if (!a.stay) store.notices.open(n.id);
            a.run();
          }}
        >
          {a.label}
        </Button>
      ))}
    </span>
  );
}

/** One toast: the same inset on every side, the status icon with a bold title and a short second line, then its buttons flush right: the main one filled, the rest grey chips. */
function Toast({ n }: { n: Notice }) {
  const store = useStore();
  const [more, setMore] = useState(false);
  const [icon, level] = MARK[n.level];
  const tone = n.live?.mark === 'restart' ? 'text-acc-ink' : level; // a ready update is the restart mark in the accent, not a warning
  const go = targetOf(n) !== null;
  const live = n.live;
  const sub = live ? live.step : secondLine(n);
  const long = !!n.details;
  return (
    <div
      className="toast relative flex items-start gap-2 rounded-md border border-line bg-panel p-3 text-md text-ink shadow-[var(--shadow)]"
      onPointerEnter={() => store.notices.hold(n.id, 'pointer')}
      onPointerLeave={() => store.notices.release(n.id, 'pointer')}
      onFocus={() => store.notices.hold(n.id, 'focus')}
      onBlur={() => store.notices.release(n.id, 'focus')}
    >
      <span className={`mt-0.5 flex flex-none ${tone}`}>
        <Icon
          name={live?.busy ? 'working' : live?.mark === 'restart' ? 'retry' : icon}
          size={16}
          aria-hidden
          className={live?.busy ? 'animate-spin motion-reduce:animate-none' : undefined}
        />
      </span>
      <div className="grid min-w-0 flex-1 gap-1">
        <span className="font-semibold">{lineOf(n)}</span>
        {sub ? <span className="text-ink-2">{sub}</span> : null}
        {live?.fraction != null && live.busy ? (
          <span
            className="h-1 overflow-hidden rounded-full bg-raised"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(live.fraction * 100)}
          >
            <span className="block h-full bg-acc transition-[width]" style={{ width: `${Math.round(live.fraction * 100)}%` }} />
          </span>
        ) : null}
        {more && n.details ? <span className="whitespace-pre-wrap text-xs text-ink-3">{n.details}</span> : null}
        {live?.actions?.length ? <Actions n={n} /> : null}
        {!live && (go || n.retry || long) ? (
          <span className="mt-0.5 flex flex-wrap justify-end gap-1.5">
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
        className="toast-close flex-none cursor-pointer border-0 bg-transparent p-0 text-ink-3 tint"
        aria-label="Close"
        onClick={() => store.notices.close(n.id)}
      >
        <Icon name="close" size={12} aria-hidden />
      </button>
    </div>
  );
}

/** How far the stack lifts: it hangs above the status bar, which is where a chat's composer and its Send button end, so it rises over the top of any composer under it. */
function useLift(shown: boolean) {
  const stack = useRef<HTMLDivElement>(null);
  const [lift, setLift] = useState(0);
  useLayoutEffect(() => {
    const at = stack.current;
    if (!shown || !at || typeof ResizeObserver === 'undefined') return;
    const measure = () => {
      const bar = at.parentElement?.getBoundingClientRect();
      const me = at.getBoundingClientRect();
      if (!bar) return;
      const tops = [...document.querySelectorAll('.compose-wrap')]
        .map((c) => c.getBoundingClientRect())
        .filter((r) => r.width && r.left < bar.right && r.right > bar.right - me.width)
        .map((r) => r.top);
      setLift(tops.length ? Math.max(0, bar.top - Math.min(...tops)) : 0);
    };
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(document.body);
    document.querySelectorAll('.compose-wrap').forEach((c) => watch.observe(c));
    return () => watch.disconnect();
  }, [shown]);
  return { stack, lift };
}

function Toasts({ hidden }: { hidden: boolean }) {
  const store = useStore();
  const toasts = useSelector((s) => s.notices.toasts());
  const [left, setLeft] = useState<ReadonlySet<number>>(new Set());
  const spare = useSpareNotices().filter((n) => !left.has(n.task));
  // The weekly-loss card steps aside to the bell after a while, so it never keeps covering the composer's Send button.
  const ids = spare.map((n) => n.task).join();
  useEffect(() => {
    if (!ids) return;
    const t = setTimeout(() => setLeft((l) => new Set([...l, ...ids.split(',').map(Number)])), SPARE_STAYS_MS);
    return () => clearTimeout(t);
  }, [ids]);
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
  const shown = (toasts.length > 0 || spare.length > 0) && !hidden;
  const { stack, lift } = useLift(shown);
  if (!shown) return null;
  return (
    <div
      ref={stack}
      className="toasts absolute right-0 bottom-full z-10 mb-2 flex w-[380px] max-w-[90vw] flex-col-reverse gap-1.5"
      style={lift ? { marginBottom: lift + 8 } : undefined}
      role="status"
    >
      {spare.map((n) => (
        <div key={n.task} className="rounded-[8px] bg-raised shadow-[var(--shadow)]">
          <SpareCard n={n} />
        </div>
      ))}
      {toasts.slice(0, MAX_TOASTS).map((n) => (
        <Toast key={n.id} n={n} />
      ))}
    </div>
  );
}

/** The weekly-loss notices the daemon has open for the person, as task rows. */
function useSpareNotices(): SpareNotice[] {
  const items = useTasks({}).data?.items;
  return (items ?? []).flatMap((t) => spareNotice(t) ?? []);
}

/** The agent's own name: `handline.architect` is `architect`. */
const shortName = (agent: string) => agent.split('.').at(-1) ?? agent;

/** The notice that an account will lose weekly use: its sentence, the first spare tasks, and a button that starts them all. It stays until Start or the X. */
function SpareCard({ n }: { n: SpareNotice }) {
  const rows = new Map((useTasks({}).data?.items ?? []).map((t) => [t.id, t]));
  const answer = useWrite('task.done');
  const reply = (evidence: string) => answer.mutate({ task: n.task, evidence, reason: true });
  const open = () => void (location.hash = `#/tasks/${n.task}`);
  return (
    <div className="notice-spare tint flex cursor-pointer items-start gap-2 px-3 py-2 text-md text-ink" data-notice="spare" onClick={open}>
      <Icon name="waiting" size={14} className="mt-0.5 flex-none text-wait" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="m-0">{n.text}</p>
        <div className="mt-1.5 flex flex-col gap-0.5">
          {n.ids.slice(0, SPARE_SHOWN).map((id) => (
            <span key={id} className="flex min-w-0 gap-1.5 text-xs text-ink-2">
              <b className="flex-none font-mono font-semibold text-ink">{`T${id}`}</b>
              <span className="min-w-0 flex-1 truncate">{rows.get(id)?.title ?? ''}</span>
              {rows.get(id) ? <span className="flex-none text-ink-3">{shortName(rows.get(id)!.to)}</span> : null}
            </span>
          ))}
          {n.ids.length > SPARE_SHOWN ? <span className="text-xs text-ink-3">{`and ${n.ids.length - SPARE_SHOWN} more`}</span> : null}
        </div>
        {n.ids.length ? (
          <Button
            variant="solid"
            size="sm"
            icon="play"
            label="Start the spare tasks"
            className="mt-2"
            disabled={answer.isPending}
            onClick={(e) => (e.stopPropagation(), reply('start'))}
          >
            {n.ids.length === 1 ? 'Start 1 task' : `Start ${n.ids.length} tasks`}
          </Button>
        ) : null}
      </div>
      <Button
        size="sm"
        icon="close"
        label="Close this notice"
        disabled={answer.isPending}
        onClick={(e) => (e.stopPropagation(), reply('dismiss'))}
      />
    </div>
  );
}
