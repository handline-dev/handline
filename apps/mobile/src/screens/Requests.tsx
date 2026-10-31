// The My requests page (the tasks by state; "Requests" is what waits on the person): every request the founder made, newest first, with its status, who holds it and what is open under it. A tap opens its task.
import { useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { ago } from '@handline/app-core';
import type { RequestRow } from '@handline/client';
import { useRequests, useServes } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { ListRow, PageHeader, Pill } from '#src/screens/parts.tsx';

type Status = RequestRow['status'];
const WORDS: Record<Status, string> = {
  waiting_on_you: 'Needs you',
  review: 'In review',
  doing: 'Doing',
  done: 'Done',
  no_work: 'Nothing to do',
  not_yet: 'No task yet',
};
const FILTERS: { id: 'all' | Status; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'waiting_on_you', label: 'Needs you' },
  { id: 'doing', label: 'Doing' },
  { id: 'review', label: 'Review' },
  { id: 'done', label: 'Done' },
  { id: 'not_yet', label: 'No task yet' },
];

/** What is under a request: open and ended tasks, its pull requests and the comments not read yet. */
const metaOf = (r: RequestRow): string =>
  [
    r.holders.length ? r.holders.join(', ') : r.agent,
    r.open ? `${r.open} open` : '',
    r.done ? `${r.done} done` : '',
    r.prs.length ? r.prs.map((n) => `#${n}`).join(' ') : '',
    r.unread_comments ? `${r.unread_comments} new ${r.unread_comments === 1 ? 'comment' : 'comments'}` : '',
    r.status === 'no_work' && r.reason ? r.reason : '',
  ]
    .filter(Boolean)
    .join(' · ');

export function RequestsPage(props: { onClose: () => void; onTask: (id: number) => void }) {
  const c = usePalette();
  const q = useRequests();
  const served = useServes('requests.list').served;
  const [filter, setFilter] = useState<'all' | Status>('all');
  const rows = q.data?.items ?? [];
  const shown = rows.filter((r) => filter === 'all' || r.status === filter);
  return (
    <View style={{ flex: 1 }}>
      <PageHeader title="My requests" back={{ label: 'Agents', onPress: props.onClose, testID: 'requests-back' }} />
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={{ flexGrow: 0 }}
        contentContainerStyle={{ gap: space.sm, paddingHorizontal: space.lg }}
      >
        {FILTERS.map((f) => (
          <Pill key={f.id} testID={`requests-filter-${f.id}`} on={filter === f.id} label={f.label} onPress={() => setFilter(f.id)} />
        ))}
      </ScrollView>
      <Loadable
        query={q}
        served={served}
        isEmpty={!shown.length}
        skeleton="tasks"
        label="your asks"
        empty={rows.length ? 'Nothing with this status' : 'You have asked for nothing yet'}
      >
        <ScrollView style={{ marginTop: space.md }}>
          {shown.map((r) => (
            <ListRow
              key={r.cursor}
              testID={`request-${r.task ?? r.ref}`}
              title={r.title}
              meta={`${WORDS[r.status]} · ${metaOf(r)}`}
              metaColor={r.status === 'waiting_on_you' ? c.wait : undefined}
              onPress={() => (r.task !== null ? props.onTask(r.task) : undefined)}
              trail={
                <View style={{ alignItems: 'flex-end' }}>
                  {r.task !== null ? <Text style={{ color: c.muted, fontSize: font.size.xs }}>T{r.task}</Text> : null}
                  <Text style={{ color: c.muted, fontSize: font.size.xs }}>{ago(r.last_activity)}</Text>
                </View>
              }
            />
          ))}
        </ScrollView>
      </Loadable>
    </View>
  );
}
