// A run of task cards drawn as one line, and what the person has seen of each line.
import { useEffect, useRef, useState } from 'react';
import type { ChatMessage, Proposal, TranscriptItem } from '@handline/client';
import { chatView, clockTime, foldTasks, type TaskFold, type TimelineEntry } from '@handline/app-core';
import { TaskLine } from '#src/ChatTaskCards.tsx';
import { rowKey } from '@handline/app-data';
import { Icon } from '#src/ui/index.ts';

/** The key of a row in the stream. */
export const entryKey = (e: TimelineEntry | TaskFold) =>
  e.kind === 'item' || e.kind === 'fold' ? `i${e.id}` : e.kind === 'proposal' ? `p${e.id}` : `m${rowKey(e.id)}`;

/** The agent's own chat shows a run of task cards as one line; the all tab keeps every card. */
export const foldedIn = (scope: string, entries: TimelineEntry[]): (TimelineEntry | TaskFold)[] =>
  scope === 'mine' ? foldTasks(entries) : entries;

/** Which lines are open, and how many cards each held when the person last opened it; a line first seen after the chat loaded counts all its cards as new. */
export function useFolds(
  chat: { status: string; messages: ChatMessage[]; items: TranscriptItem[]; moreMessages: boolean; moreItems: boolean },
  scope: 'mine' | 'all',
  agent: string,
  me: string,
  decided: readonly Proposal[],
) {
  const ready = chat.status === 'ready';
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());
  const seen = useRef(new Map<number, number>());
  const loaded = useRef(false);
  useEffect(() => {
    if (ready) loaded.current = true;
  });
  return {
    entries: foldedIn(scope, chatView(chat, scope, agent, me, decided).entries),
    open: (f: TaskFold) => open.has(f.id),
    fresh: (f: TaskFold) => {
      if (!seen.current.has(f.id)) seen.current.set(f.id, loaded.current ? 0 : f.cards.length);
      return f.cards.length - seen.current.get(f.id)!;
    },
    toggle: (f: TaskFold) => {
      seen.current.set(f.id, f.cards.length);
      setOpen((s) => new Set(s.has(f.id) ? [...s].filter((x) => x !== f.id) : [...s, f.id]));
    },
  };
}

/** A run of task cards as one line (`3 tasks · 2 added · 1 done`, with the time range and `N new`); a tap opens the cards in place. */
export function FoldLine(props: {
  f: TaskFold;
  titles?: Map<number, string>;
  agent?: string;
  folds: Omit<ReturnType<typeof useFolds>, 'entries'>;
}) {
  const { f, folds } = props;
  const open = folds.open(f);
  /** Cards that joined since the person last opened this line. */
  const fresh = folds.fresh(f);
  const range = f.at === f.until ? clockTime(f.at) : `${clockTime(f.at)}–${clockTime(f.until)}`;
  const parts = [
    `${f.tasks} ${f.tasks === 1 ? 'task' : 'tasks'}`,
    f.added ? `${f.added} added` : '',
    f.done ? `${f.done} done` : '',
  ].filter(Boolean);
  return (
    <div className="card-fold flex flex-col items-start gap-1" data-kind="task_fold" data-id={`f${f.id}`}>
      <button
        type="button"
        aria-expanded={open}
        className="group flex w-fit max-w-full cursor-pointer flex-wrap items-baseline gap-x-2 rounded-md border border-line bg-panel px-2.5 py-1 text-left text-md"
        onClick={() => folds.toggle(f)}
      >
        <Icon name={open ? 'less' : 'more'} size={14} label={open ? 'Fold the task cards' : 'Show the task cards'} />
        <span className="font-medium">{parts.join(' · ')}</span>
        <span className="muted card-time hidden group-hover:inline group-focus-within:inline">{range}</span>
        {!open && fresh > 0 ? <span className="card-new text-meta font-semibold text-accent">{fresh} new</span> : null}
      </button>
      {open ? props.f.rows.map((r) => <TaskLine key={r.id} i={r.item} titles={props.titles} agent={props.agent} />) : null}
    </div>
  );
}
