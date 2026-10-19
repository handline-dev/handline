// A task's past: the history rows a patch to it makes (one per field it changes, as the daemon keeps them), and task.time's segments.
import type { HistoryRow, Task, TimeSegment } from '@handline/contract';
import { invalidParams } from './errors.ts';

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

// task.time: queued until the task was delivered, then working, to its done or now.
interface Tasks {
  find(id: unknown): Pick<Task, 'id' | 'created' | 'delivered' | 'done'>;
  now(): string;
}

export function timeOf(id: unknown, tasks: Tasks): { task: number; start: string; end: string | null; segments: TimeSegment[] } {
  if (!Number.isInteger(id)) throw invalidParams('task is a task number', 'task');
  const t = tasks.find(id);
  const end = t.done ?? tasks.now();
  const seg = (kind: 'queued' | 'working', from: string, to: string): TimeSegment => ({
    kind,
    on: null,
    text: kind === 'queued' ? 'queued' : '',
    task: null,
    from,
    to,
    seconds: Math.round((Date.parse(to) - Date.parse(from)) / 1000),
  });
  const made = t.delivered ? [seg('queued', t.created, t.delivered), seg('working', t.delivered, end)] : [seg('queued', t.created, end)];
  return { task: t.id, start: t.created, end: t.done, segments: made.filter((s) => s.seconds > 0) };
}
