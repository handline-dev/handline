// The history rows a patch to a task makes: one per field it changes, as the daemon keeps them.
import type { HistoryRow, Task } from '@handline/contract';

type Fields = Task & { body: string; blockers: { task: number }[] };

export function changes(t: Fields, patch: Partial<Fields>, at: string, by: string): HistoryRow[] {
  const rows: HistoryRow[] = [];
  const log = (field: HistoryRow['field'], was: unknown, now: unknown) => {
    if (String(was ?? '') !== String(now ?? '')) rows.push({ at, by, field, was: String(was ?? ''), now: String(now ?? '') });
  };
  if ('title' in patch) log('title', t.title, patch.title);
  if ('to' in patch) log('assignee', t.to, patch.to);
  if ('body' in patch) log('body', t.body, patch.body);
  if ('priority' in patch) log('priority', t.priority ? `P${t.priority}` : '', patch.priority ? `P${patch.priority}` : '');
  if ('time_box' in patch) log('timeout', t.time_box, patch.time_box);
  if ('state' in patch) log('state', t.state, patch.state);
  if (patch.blockers && patch.blockers.length > t.blockers.length)
    log('blocked_by', '', `T${patch.blockers.find((b) => !t.blockers.some((o) => o.task === b.task))?.task}`);
  return rows;
}
