// "My requests" on the Tasks page: the founder's own open tasks (not their subtasks), by what is happening to each, every row with one line that
// says how far it is, who works on it now, or what it waits on. A row opens the task as any row of Tasks does.
import { CircleAlert, X } from 'lucide-react';
import { taskRef } from '@handline/app-core';
import type { RequestRow } from '@handline/client';
import { useRequests, useTasks } from '@handline/app-data';
import { goTasks } from '#src/route.ts';
import { Chip } from '#src/ds/Chip.tsx';
import { SectionHead } from '#src/ds/ListRow.tsx';

const GROUPS = ['Waiting on you', 'Blocked', 'Being worked', 'Not taken yet'] as const;
type Group = (typeof GROUPS)[number];

const short = (agent: string) => agent.split('.').at(-1) ?? agent;
/** The daemon's `now` and `blocked` (the agent at work under the request, and that it waits): absent from one that does not send them. */
const nowOf = (r: RequestRow) => (typeof r.now === 'string' ? r.now : (r.holders[0] ?? null));
const blockedNow = (r: RequestRow) => r.blocked === true;

function groupOf(r: RequestRow): Group {
  if (r.status === 'waiting_on_you') return 'Waiting on you';
  if (blockedNow(r)) return 'Blocked';
  return nowOf(r) ? 'Being worked' : 'Not taken yet';
}

/** The founder's open tasks, grouped in the order the list reads. */
export function useMyRequests(): { rows: RequestRow[]; groups: { name: Group; rows: RequestRow[] }[]; pending: boolean } {
  const q = useRequests();
  const rows = (q.data?.items ?? []).filter((r) => r.kind === 'task' && r.task != null && r.status !== 'done' && r.status !== 'no_work');
  return {
    rows,
    groups: GROUPS.map((name) => ({ name, rows: rows.filter((r) => groupOf(r) === name) })).filter((g) => g.rows.length),
    pending: q.isPending,
  };
}

/** "My requests 6" beside Open and Closed; on, it reads "My requests 6 ×" and the × takes it off. */
export function MyRequestsChip({ count, on, onToggle }: { count: number; on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      title={on ? 'Show all tasks' : 'Only the tasks you asked for'}
      onClick={onToggle}
      className={`inline-flex h-7 cursor-pointer items-center gap-1 rounded-sm border border-line-strong px-2 text-sm focus-visible:outline-2 focus-visible:outline-acc ${on ? 'bg-panel font-medium text-ink' : 'bg-transparent text-ink-2 tint'}`}
    >
      My requests <span className="text-ink-3">{count}</span>
      {on ? <X width={12} height={12} strokeWidth={1.75} aria-hidden /> : null}
    </button>
  );
}

function Line({ r }: { r: RequestRow }) {
  const waits = (useTasks({}).data?.items ?? []).find((t) => t.id === r.task)?.blocked_by?.[0];
  if (blockedNow(r) || waits != null)
    return (
      <span className="flex items-center gap-1 text-xs text-wait" data-line="blocked">
        <CircleAlert width={12} height={12} strokeWidth={1.75} aria-hidden />
        {waits != null ? (
          <>
            Blocked on{' '}
            <button
              type="button"
              className="cursor-pointer border-0 bg-transparent p-0 font-mono text-xs text-wait underline"
              onClick={(e) => (e.stopPropagation(), goTasks(waits))}
            >
              {taskRef(waits)}
            </button>
          </>
        ) : (
          'Blocked'
        )}
      </span>
    );
  const total = r.open + r.done;
  const line = [
    total ? `${r.done} of ${total} done` : '',
    nowOf(r) ? `now ${short(nowOf(r)!)}` : r.status === 'waiting_on_you' ? 'waiting on you' : 'not taken yet',
    r.status === 'review' ? 'in review' : '',
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <span className="text-xs text-ink-3" data-line="progress">
      {line}
    </span>
  );
}

/** The list the chip turns on, in place of the table. */
export function MyRequestsList({ groups, pending }: { groups: { name: Group; rows: RequestRow[] }[]; pending: boolean }) {
  if (pending) return <p className="muted px-4">Loading…</p>;
  if (!groups.length)
    return (
      <div className="empty">
        <strong>No open request of yours</strong>
        <p>What you ask an agent for shows here, with how far it is.</p>
      </div>
    );
  return (
    <div className="min-h-0 flex-1 overflow-auto px-2 pb-6" aria-label="My requests">
      {groups.map((g) => (
        <section key={g.name} aria-label={g.name}>
          <SectionHead count={g.rows.length}>
            <span className={g.name === 'Blocked' ? 'text-wait' : undefined}>{g.name}</span>
          </SectionHead>
          {g.rows.map((r) => (
            <button
              key={r.task}
              type="button"
              data-request={r.task}
              onClick={() => goTasks(r.task!)}
              className="flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-sm border-0 bg-transparent px-3 py-1.5 text-left tint focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-acc"
            >
              <span className="flex w-full min-w-0 gap-3 text-md text-ink">
                <span className="w-14 flex-none font-mono">{taskRef(r.task!)}</span>
                <span className="min-w-0 flex-1 truncate">{r.title}</span>
                {r.prs.length ? <Chip>{r.prs.map((n) => `#${n}`).join(' ')}</Chip> : null}
              </span>
              <span className="pl-[calc(3.5rem+0.75rem)]">
                <Line r={r} />
              </span>
            </button>
          ))}
        </section>
      ))}
    </div>
  );
}
