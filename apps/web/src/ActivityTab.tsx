// The Activity tab: what this agent does with the team, read-only, one dim line per item with chips to filter by kind. Steering happens in Chat.
import { useEffect, useRef, useState } from 'react';
import { ACTIVITY_KINDS, teamActivityOf, clockTime, type ActivityKind } from '@handline/app-core';
import { useChat } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { Empty } from '#src/ui/index.ts';

const CHIP = 'cursor-pointer rounded-pill border border-line bg-transparent px-2.5 py-0.5 text-label text-ink-2 aria-pressed:bg-panel aria-pressed:text-ink';
const LABEL: Record<ActivityKind, string> = { messages: 'Messages', tasks: 'Tasks', sessions: 'Sessions', tools: 'Tools' };

export function ActivityTab({ agent, row = null }: { agent: string; row?: string | null }) {
  const me = useStore().me;
  const chat = useChat(agent, 'all');
  const [only, setOnly] = useState<ActivityKind | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const rows = teamActivityOf(chat.messages, chat.items, me).filter((r) => !only || r.kind === only);
  // A row named by a message link: scrolled to and flashed once it is drawn.
  const shown = useRef<string | null>(null);
  const hit = row ? rows.some((r) => r.key === row) : false;
  useEffect(() => {
    if (!row || !hit || shown.current === row) return;
    shown.current = row;
    const el = document.querySelector<HTMLElement>(`.activity [data-row="${CSS.escape(row)}"]`);
    el?.scrollIntoView?.({ block: 'center' });
    el?.setAttribute('data-flash', '');
    setTimeout(() => el?.removeAttribute('data-flash'), 2000);
  }, [row, hit]);
  return (
    <section className="activity flex min-h-0 flex-col" aria-label={`Activity of ${agent}`}>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2" role="group" aria-label="Show">
        <button className={CHIP} aria-pressed={only === null} onClick={() => setOnly(null)}>
          All
        </button>
        {ACTIVITY_KINDS.map((k) => (
          <button key={k} className={CHIP} aria-pressed={only === k} onClick={() => setOnly(only === k ? null : k)}>
            {LABEL[k]}
          </button>
        ))}
      </div>
      <ul className="m-0 flex min-h-0 flex-1 list-none flex-col gap-0.5 overflow-auto p-3">
        {chat.status === 'ready' && !rows.length ? <Empty icon="team" name="Nothing here yet" /> : null}
        {rows.map((r) => (
          <li key={r.key} data-kind={r.kind} data-row={r.key} className="border-b border-line py-1.5 text-body data-[flash]:outline-2 data-[flash]:-outline-offset-2 data-[flash]:outline-solid data-[flash]:outline-focus">
            <button
              className="flex w-full cursor-pointer items-baseline gap-2 border-0 bg-transparent p-0 text-left text-inherit"
              aria-expanded={open === r.key}
              onClick={() => setOpen(open === r.key ? null : r.key)}
            >
              <time className="mono flex-none text-hint text-ink-2" dateTime={r.at}>
                {clockTime(r.at)}
              </time>
              {r.route ? (
                <strong className="flex-none font-medium">
                  {r.route.from} → {r.route.to}
                </strong>
              ) : only === null ? (
                <span className="flex-none rounded-ctl border border-line px-1.5 text-hint text-ink-2">{r.kind.slice(0, -1)}</span>
              ) : null}
              <span className={`min-w-0 flex-1 text-ink-2 ${open === r.key ? 'whitespace-pre-wrap' : 'truncate'}`}>{r.text}</span>
            </button>
            {open === r.key && r.output ? <pre className="tool-output mono m-0 mt-1 overflow-auto whitespace-pre-wrap text-hint text-ink-2">{r.output}</pre> : null}
            {open === r.key && r.dropped ? <p className="m-0 text-hint text-wait">{r.dropped} more lines not shown</p> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
