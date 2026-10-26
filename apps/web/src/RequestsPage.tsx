// The Requests page (#/requests): every request the founder made, newest first, each with its status, who holds it and what is open under it. A
// request opens its task; a message of theirs that is not a task yet offers Make a request and No work. New request asks an agent for something.
import { useMemo, useState } from 'react';
import { ago } from '@handline/app-core';
import type { RequestRow } from '@handline/client';
import { classify, sentence, useAgents, useRequests, useWrite } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { NewTask } from '#src/NewTask.tsx';
import { goTasks } from '#src/route.ts';
import { Button, Empty, Input, PageHead, PageTitle, ScrollPage, Segmented, State, type StateKind } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

type Status = RequestRow['status'];
const WORDS: Record<Status, { label: string; glyph: StateKind }> = {
  waiting_on_you: { label: 'Waiting on you', glyph: 'needs' },
  review: { label: 'In review', glyph: 'working' },
  doing: { label: 'Doing', glyph: 'working' },
  done: { label: 'Done', glyph: 'done' },
  no_work: { label: 'No work', glyph: 'idle' },
  not_yet: { label: 'Not a task yet', glyph: 'idle' },
};
const FILTERS: { value: 'all' | Status; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'waiting_on_you', label: 'Waiting on you' },
  { value: 'doing', label: 'Doing' },
  { value: 'review', label: 'Review' },
  { value: 'done', label: 'Done' },
  { value: 'not_yet', label: 'Not yet' },
];

/** What is under a request: open and ended tasks, its pull requests and the comments the founder has not read. */
const meta = (r: RequestRow): string =>
  [
    r.open || r.done ? `${r.open} open · ${r.done} done` : '',
    r.prs.length ? r.prs.map((n) => `#${n}`).join(' ') : '',
    r.unread_comments ? `${r.unread_comments} new ${r.unread_comments === 1 ? 'comment' : 'comments'}` : '',
  ]
    .filter(Boolean)
    .join(' · ');

/** A message that is not a task yet: turn it into a request, or say why it needs no work. */
function Undecided({ row }: { row: RequestRow }) {
  const make = useWrite('task.request');
  const none = useWrite('task.none');
  const [why, setWhy] = useState<string | null>(null);
  const failed = [make, none].find((w) => w.isError)?.error;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <Button label="Make a request" disabled={make.isPending} onClick={() => make.mutate({ request: row.ref! })} />
      {why === null ? (
        <Button label="No work" variant="outline" onClick={() => setWhy('')} />
      ) : (
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (why.trim()) none.mutate({ request: row.ref!, reason: why.trim() });
          }}
        >
          <Input aria-label="Why no work" placeholder="why no work is needed" value={why} onChange={(e) => setWhy(e.target.value)} />
          <Button type="submit" label="Mark no work" disabled={none.isPending || !why.trim()} />
        </form>
      )}
      {failed ? (
        <span role="alert" className="text-hint text-bad">
          {sentence(classify(failed))}
        </span>
      ) : null}
    </div>
  );
}

function Row({ row }: { row: RequestRow }) {
  const w = WORDS[row.status];
  const open = row.task !== null ? () => goTasks(row.task!) : undefined;
  return (
    <li
      className="request-row rounded-card border border-line bg-panel px-3 py-2.5"
      data-status={row.status}
      data-request={row.task ?? row.ref}
    >
      <div
        {...(open
          ? {
              role: 'button',
              tabIndex: 0,
              'aria-label': `Open T${row.task}`,
              onClick: open,
              onKeyDown: (e: React.KeyboardEvent) => (e.key === 'Enter' || e.key === ' ') && open(),
            }
          : {})}
        className={open ? 'cursor-pointer' : undefined}
      >
        <div className="flex min-w-0 items-center gap-2">
          <State kind={w.glyph} label={w.label} size={14} />
          <span className="text-hint text-ink-2">{w.label}</span>
          <h3 className="m-0 min-w-0 flex-1 truncate text-[15px] font-medium">{row.title}</h3>
          {row.task !== null ? <span className="mono text-hint text-ink-2">T{row.task}</span> : null}
          <span className="text-hint text-ink-3">{ago(row.last_activity)}</span>
        </div>
        <p className="muted m-0 mt-0.5 flex flex-wrap gap-x-3 text-hint">
          <span className="mono">{row.holders.length ? row.holders.join(', ') : row.agent}</span>
          {meta(row) ? <span>{meta(row)}</span> : null}
          {row.status === 'no_work' && row.reason ? <span>{row.reason}</span> : null}
        </p>
      </div>
      {row.kind === 'message' && row.status === 'not_yet' && row.ref ? <Undecided row={row} /> : null}
    </li>
  );
}

export function RequestsPage() {
  const q = useRequests();
  const agents = useAgents();
  const [filter, setFilter] = useState<'all' | Status>('all');
  const [asking, setAsking] = useState(false);
  const served = useSelector((s) => s.can('requests.list'));
  const rows = q.data?.items;
  const shown = useMemo(() => (rows ?? []).filter((r) => filter === 'all' || r.status === filter), [rows, filter]);
  const leads = (agents.data?.agents ?? []).filter((a) => !a.parent).map((a) => a.name);
  return (
    <ScrollPage className="requests-page" aria-label="Requests">
      <PageHead>
        <PageTitle>Requests</PageTitle>
        <Button icon="plus" label="New request" onClick={() => setAsking(true)} />
      </PageHead>
      <Segmented label="Show" value={filter} onChange={setFilter} options={FILTERS} />
      <View q={q} served={served} shape="tasks" what="requests">
        {shown.length ? (
          <ul className="mx-0 mt-3 mb-0 flex list-none flex-col gap-2 p-0">
            {shown.map((r) => (
              <Row key={r.cursor} row={r} />
            ))}
          </ul>
        ) : (
          <Empty icon="tasks" name={rows?.length ? 'Nothing with this status' : 'No requests yet'} />
        )}
      </View>
      {asking ? (
        <NewTask request agents={leads.length ? leads : (agents.data?.agents ?? []).map((a) => a.name)} onClose={() => setAsking(false)} />
      ) : null}
    </ScrollPage>
  );
}
