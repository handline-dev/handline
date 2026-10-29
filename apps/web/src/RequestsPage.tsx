// The Requests page (#/requests): every request the founder made, grouped by where it stands, each with who holds it and what is open under it.
// A request opens its task; the lead turns a message of theirs into one. New request asks an agent for something.
import { Plus } from 'lucide-react';
import { useMemo, useState } from 'react';
import { ago } from '@handline/app-core';
import type { RequestRow } from '@handline/client';
import { useAgents, useRequests } from '@handline/app-data';
import { useSelector } from '@handline/app-react';
import { Button } from '#src/ds/Button.tsx';
import { Chip } from '#src/ds/Chip.tsx';
import { Segmented } from '#src/ds/Field.tsx';
import { EmptyList, ListRow, SectionHead } from '#src/ds/ListRow.tsx';
import { StateMark, type StateName } from '#src/ds/StateMark.tsx';
import { NewTask } from '#src/NewTask.tsx';
import { goTasks } from '#src/route.ts';
import { PageHead, PageTitle, ScrollPage } from '#src/ui/index.ts';
import { View } from '#src/View.tsx';

type Status = RequestRow['status'];
const WORDS: Record<Status, { label: string; state: StateName }> = {
  waiting_on_you: { label: 'Needs you', state: 'needs' },
  review: { label: 'In review', state: 'working' },
  doing: { label: 'Doing', state: 'working' },
  done: { label: 'Done', state: 'done' },
  no_work: { label: 'Nothing to do', state: 'dropped' },
  not_yet: { label: 'No task yet', state: 'idle' },
};
/** The groups, in the order the founder reads them: what waits on them first, closed last. */
const GROUPS: { title: string; of: Status[] }[] = [
  { title: 'Waiting on you', of: ['waiting_on_you'] },
  { title: 'Being worked', of: ['review', 'doing'] },
  { title: 'Not taken yet', of: ['not_yet'] },
  { title: 'Done', of: ['done'] },
  { title: 'Nothing to do', of: ['no_work'] },
];
const FILTERS: { value: 'all' | Status; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'waiting_on_you', label: 'Needs you' },
  { value: 'doing', label: 'Doing' },
  { value: 'review', label: 'Review' },
  { value: 'done', label: 'Done' },
  { value: 'not_yet', label: 'No task yet' },
];

/** What is under a request: open and ended tasks, its pull requests and the comments the founder has not read. */
const meta = (r: RequestRow): string =>
  [
    r.open ? `${r.open} open` : '',
    r.done ? `${r.done} done` : '',
    r.prs.length ? r.prs.map((n) => `#${n}`).join(' ') : '',
    r.unread_comments ? `${r.unread_comments} new ${r.unread_comments === 1 ? 'comment' : 'comments'}` : '',
    r.status === 'no_work' && r.reason ? r.reason : '',
  ]
    .filter(Boolean)
    .join(' · ');

function Row({ row }: { row: RequestRow }) {
  const w = WORDS[row.status];
  const closed = row.status === 'done' || row.status === 'no_work';
  const mark = (
    <span className="flex w-28 shrink-0 items-center">
      <StateMark state={w.state} word label={w.label} />
    </span>
  );
  const chip = (
    <span className="flex min-w-0 shrink-0 items-center gap-2 text-xs text-ink-3">
      {meta(row) ? <span className="truncate">{meta(row)}</span> : null}
      <Chip>{row.holders.length ? row.holders.join(', ') : row.agent}</Chip>
      {row.task !== null ? <span className="font-mono text-ink-2">T{row.task}</span> : null}
    </span>
  );
  const time = ago(row.last_activity);
  return (
    <li className="request-row" data-status={row.status} data-request={row.task ?? row.ref}>
      {row.task !== null ? (
        <ListRow
          lead={mark}
          title={row.title}
          chip={chip}
          time={time}
          done={closed}
          label={`Open T${row.task}`}
          onOpen={() => goTasks(row.task!)}
        />
      ) : (
        <div className="flex h-9 items-center gap-3 px-3 text-md">
          {mark}
          <span className={`min-w-0 flex-1 truncate ${closed ? 'text-ink-3' : ''}`}>{row.title}</span>
          {chip}
          <span className="w-[2.4em] text-right text-xs text-ink-3">{time}</span>
        </div>
      )}
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
        <Button iconOnly icon={Plus} label="New request" size="s" onClick={() => setAsking(true)} />
      </PageHead>
      <Segmented label="Show" value={filter} onChange={setFilter} choices={FILTERS} />
      <View q={q} served={served} shape="tasks" what="requests">
        {shown.length ? (
          GROUPS.map((g) => {
            const list = shown.filter((r) => g.of.includes(r.status));
            return list.length ? (
              <section key={g.title} aria-label={g.title}>
                <SectionHead count={list.length}>{g.title}</SectionHead>
                <ul className="m-0 flex list-none flex-col p-0">
                  {list.map((r) => (
                    <Row key={r.cursor} row={r} />
                  ))}
                </ul>
              </section>
            ) : null;
          })
        ) : (
          <EmptyList>{rows?.length ? 'Nothing with this status' : 'No requests yet'}</EmptyList>
        )}
      </View>
      {asking ? (
        <NewTask request agents={leads.length ? leads : (agents.data?.agents ?? []).map((a) => a.name)} onClose={() => setAsking(false)} />
      ) : null}
    </ScrollPage>
  );
}
