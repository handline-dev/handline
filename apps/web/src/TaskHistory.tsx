// The change history of one task: a row per field changed (who, what, before struck through, after in bold), the fields of one edit under one
// time, memo changes folded to a line under All, filter chips and day dividers. Read from task.history; the long text opens to its full before and after.
import { useState } from 'react';
import {
  clockTime,
  dayLabel,
  firstLine,
  historyCounts,
  historyView,
  memoAction,
  type HistoryFilter,
  type HistoryFold,
} from '@handline/app-core';
import type { HistoryRow } from '@handline/client';
import { StateChip } from '#src/taskui.tsx';
import { Reveal } from '#src/ui/Reveal.tsx';

const FILTERS: [HistoryFilter, string][] = [
  ['all', 'All'],
  ['fields', 'Fields'],
  ['state', 'State'],
  ['memo', 'Comment'],
];
const CHIP =
  'inline-flex items-center gap-1 rounded-full bg-transparent border border-line px-2.5 py-0.5 text-ink-2 aria-pressed:border-ink aria-pressed:bg-raised aria-pressed:text-ink';
const ROW = 'grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 border-t border-line py-1.5 max-sm:grid-cols-1';
const WAS = 'text-ink-3 line-through';
const FIELD_WORD: Record<string, string> = { timeout: 'time box', blocked_by: 'waits on', merged_into: 'merged into' };

const Value = ({ state, text, was }: { state: boolean; text: string; was?: boolean }) =>
  state && text ? (
    <span className={was ? 'inline-flex line-through opacity-70' : 'inline-flex'}>
      <StateChip state={text} word />
    </span>
  ) : (
    <span className={was ? WAS : 'font-semibold'}>{text || '—'}</span>
  );

/** One changed field: who, which, from what to what; a long text shows its first line until it is clicked. */
function Change({ r, shown }: { r: HistoryRow; shown: boolean }) {
  const [open, setOpen] = useState(false);
  const memo = r.field === 'memo';
  const state = r.field === 'state';
  const action = memo ? memoAction(r) : null;
  const was = firstLine(r.was);
  const now = firstLine(r.now);
  const long = was.more || now.more;
  const label = memo ? `comment #${r.memo}` : (FIELD_WORD[r.field] ?? r.field);
  const body = memo ? (action === 'deleted' ? r.was : r.now) : null;
  return (
    <div className="contents" data-field={r.field}>
      <span className="mono whitespace-nowrap text-ink-3">{shown ? clockTime(r.at) : ''}</span>
      <div className="min-w-0">
        <div
          className={`flex flex-wrap items-baseline gap-x-2 ${long ? 'cursor-pointer' : ''}`}
          onClick={long ? () => setOpen(!open) : undefined}
          role={long ? 'button' : undefined}
          tabIndex={long ? 0 : undefined}
          aria-expanded={long ? open : undefined}
          onKeyDown={long ? (e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setOpen(!open)) : undefined}
        >
          <span>{r.by}</span>
          <span className="text-ink-3">{label}</span>
          {memo ? (
            <>
              <strong>{action}</strong>
              {action === 'edited' ? <span className={WAS}>{was.head}</span> : null}
              {action === 'edited' ? (
                <strong>{now.head}</strong>
              ) : (
                <span className={action === 'deleted' ? 'text-bad' : 'font-semibold'}>{firstLine(body!).head}</span>
              )}
            </>
          ) : (
            <>
              <span className={r.was ? undefined : 'text-ink-3'}>
                <Value state={state} text={was.head} was />
              </span>
              <span className="text-ink-3" aria-label="to">
                →
              </span>
              <Value state={state} text={now.head} />
              {state && !r.was && r.now !== 'opened' ? <span className="text-ink-3">· opened</span> : null}
            </>
          )}
          {long ? (
            <span className="text-ink-3" aria-hidden="true">
              {open ? '▾' : '▸'}
            </span>
          ) : null}
        </div>
        {r.note ? <p className="m-0 mt-0.5 border-l-2 border-line pl-2 text-ink-2">{firstLine(r.note, 160).head}</p> : null}
        {open ? (
          <div className="mt-1 grid gap-1 rounded bg-raised p-2" data-full>
            {r.was ? <div className={`${WAS} whitespace-pre-wrap`}>{r.was}</div> : null}
            {r.now ? <div className="font-semibold whitespace-pre-wrap">{r.now}</div> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Fold({ fold }: { fold: HistoryFold }) {
  const [open, setOpen] = useState(false);
  const n = fold.rows.length;
  const range = fold.from === fold.to ? clockTime(fold.to) : `${clockTime(fold.from)} to ${clockTime(fold.to)}`;
  return (
    <div className="border-t border-line py-1.5">
      <button
        type="button"
        className="w-full rounded bg-raised px-2 py-1 text-left"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        data-fold
      >
        <span aria-hidden="true">{open ? '▾ ' : '▸ '}</span>
        <strong>{n === 1 ? '1 comment change' : `${n} comment changes`}</strong> by {fold.by.join(', ')} · {range}
      </button>
      <Reveal show={open}>
        <div className="mt-1 grid grid-cols-[5rem_minmax(0,1fr)] gap-x-2 gap-y-1.5 max-sm:grid-cols-1">
          {fold.rows.map((r, j) => (
            <Change key={j} r={r} shown />
          ))}
        </div>
      </Reveal>
    </div>
  );
}

export function TaskHistory({ rows }: { rows: HistoryRow[] }) {
  const [filter, setFilter] = useState<HistoryFilter>('all');
  const [newest, setNewest] = useState(true);
  const counts = historyCounts(rows);
  const days = historyView(rows, filter, newest);
  return (
    <div className="thistory" data-filter={filter}>
      <div className="mb-2 flex flex-wrap items-center gap-1.5" role="group" aria-label="Show changes">
        {FILTERS.map(([k, word]) => (
          <button key={k} type="button" className={CHIP} aria-pressed={filter === k} onClick={() => setFilter(k)}>
            {word} <span className="opacity-70">{counts[k]}</span>
          </button>
        ))}
        <button type="button" className={`${CHIP} ml-auto`} onClick={() => setNewest(!newest)}>
          {newest ? 'Newest first' : 'Oldest first'}
        </button>
      </div>
      {days.map((d) => (
        <section key={d.day} aria-label={dayLabel(d.day)}>
          <h4 className="m-0 mt-2 mb-0.5 text-xs font-normal tracking-wide text-ink-3 uppercase">{dayLabel(d.day)}</h4>
          {d.items.map((item, i) =>
            item.kind === 'memos' ? (
              <Fold key={`${d.day}-${i}`} fold={item} />
            ) : (
              <div key={`${d.day}-${i}`} className={ROW}>
                {item.rows.map((r, j) => (
                  <Change key={j} r={r} shown={j === 0} />
                ))}
              </div>
            ),
          )}
        </section>
      ))}
    </div>
  );
}
