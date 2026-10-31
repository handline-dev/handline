// "From you" on the Requests page: the founder's own requests (requests.list), by what is happening to them, each with how far its tasks are.
// A row opens its task beside the list, with the one line that says who holds it now.
import { useState } from 'react';
import { ago, taskRef } from '@handline/app-core';
import type { RequestRow } from '@handline/client';
import { useRequests } from '@handline/app-data';
import { TaskDetail } from '#src/TaskDetail.tsx';
import { ListRow, SectionHead } from '#src/ds/ListRow.tsx';
import { useNow } from '#src/Gauge.tsx';
import { PageHead, PageTitle, Segmented } from '#src/ui/index.ts';

export type RequestsView = 'for_you' | 'from_you';
type Status = RequestRow['status'];

const GROUPS: { name: string; of: Status[] }[] = [
  { name: 'Waiting on you', of: ['waiting_on_you'] },
  { name: 'Being worked', of: ['review', 'doing'] },
  { name: 'Not taken yet', of: ['not_yet'] },
  { name: 'Done', of: ['done'] },
  { name: 'Nothing to do', of: ['no_work'] },
];
const CLOSED: Status[] = ['done', 'no_work'];
const short = (agent: string) => agent.split('.').at(-1) ?? agent;
const keyOf = (r: RequestRow) => String(r.task ?? r.ref);

/** "2 of 5" when the request has tasks under it. */
const progress = (r: RequestRow) => (r.open + r.done ? `${r.done} of ${r.open + r.done}` : '');

/** The line over an open request: how far it is, who holds it now, and its pull requests. */
export function requestLine(r: RequestRow): string {
  return [
    progress(r) && `${progress(r)} done`,
    r.holders.length ? `now ${r.holders.map(short).join(', ')}` : '',
    r.status === 'review' ? 'in review' : '',
    r.prs.length ? r.prs.map((n) => `#${n}`).join(' ') : '',
    r.status === 'no_work' && r.reason ? r.reason : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

/** For you | From you, with the count of what waits on the person. */
export function RequestsViewSwitch({ view, onView, forYou }: { view: RequestsView; onView: (v: RequestsView) => void; forYou: number }) {
  return (
    <Segmented
      label="Requests view"
      value={view}
      onChange={(v) => onView(v as RequestsView)}
      options={[
        { value: 'for_you', label: forYou ? `For you ${forYou}` : 'For you' },
        { value: 'from_you', label: 'From you' },
      ]}
    />
  );
}

/** The list of the founder's requests, and the open one's task beside it (below the list on a narrow window). */
export function FromYou({ wide, switcher }: { wide: boolean; switcher: React.ReactNode }) {
  const now = useNow();
  const q = useRequests();
  const [closed, setClosed] = useState(false);
  const [pick, setPick] = useState<string | null>(null);
  const rows = (q.data?.items ?? []).filter((r) => CLOSED.includes(r.status) === closed);
  const sel = rows.find((r) => keyOf(r) === pick) ?? (wide ? rows[0] : undefined);
  const list = (
    <section aria-label="From you" className="h-full min-w-0 flex-1 overflow-auto px-4 pb-6">
      <PageHead>
        <PageTitle>Requests</PageTitle>
        {switcher}
      </PageHead>
      <div className="mt-2 mb-1">
        <Segmented
          label="Open or closed"
          value={closed ? 'closed' : 'open'}
          onChange={(v) => setClosed(v === 'closed')}
          options={[
            { value: 'open', label: 'Open' },
            { value: 'closed', label: 'Closed' },
          ]}
        />
      </div>
      {q.isPending ? (
        <p className="muted">Loading…</p>
      ) : rows.length ? (
        GROUPS.map((g) => {
          const group = rows.filter((r) => g.of.includes(r.status));
          return group.length ? (
            <div key={g.name} role="group" aria-label={g.name}>
              <SectionHead count={group.length}>{g.name}</SectionHead>
              {group.map((r) => (
                <div key={keyOf(r)} data-request-row={keyOf(r)}>
                  <ListRow
                    title={r.title}
                    chip={
                      <span className="flex flex-none items-baseline gap-2 text-xs text-ink-3">
                        {r.task != null ? <span className="font-mono text-ink-2">{taskRef(r.task)}</span> : null}
                        {progress(r)}
                      </span>
                    }
                    time={ago(r.last_activity, now)}
                    selected={sel && keyOf(sel) === keyOf(r)}
                    done={closed}
                    label={r.title}
                    onOpen={() => setPick(keyOf(r))}
                  />
                </div>
              ))}
            </div>
          ) : null;
        })
      ) : (
        <div className="empty">
          <strong>{closed ? 'Nothing closed yet' : 'You have asked for nothing open'}</strong>
          <p>What you ask an agent for shows here, with how far it is.</p>
        </div>
      )}
    </section>
  );
  const detail = sel ? (
    <div className="min-h-0 min-w-0 flex-1 overflow-auto" data-request={keyOf(sel)}>
      {requestLine(sel) ? <p className="request-line m-0 px-5 pt-4 text-sm text-ink-2">{requestLine(sel)}</p> : null}
      {sel.task != null ? <TaskDetail id={sel.task} embedded /> : <p className="m-0 px-5 py-2 text-md text-ink">{sel.title}</p>}
    </div>
  ) : null;
  return wide ? (
    <div className="flex h-full min-h-0 min-w-0 flex-1">
      <div className="flex min-h-0 w-[min(420px,40%)] flex-none flex-col">{list}</div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col border-l border-line">{detail}</div>
    </div>
  ) : pick && detail ? (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <button type="button" className="m-2 w-fit cursor-pointer border-0 bg-transparent p-1 text-sm text-acc" onClick={() => setPick(null)}>
        ← From you
      </button>
      {detail}
    </div>
  ) : (
    list
  );
}
