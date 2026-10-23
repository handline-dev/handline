// The task cards of the conversation: a task event from the transcript and the task board's news of a task, each as one compact bar.
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { clockTime, isPerson, openTask, type TaskChange, taskRow } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { Icon, type IconName } from '#src/ui/index.ts';
import { useWorkspace } from '#src/Workspace.tsx';

const TASK_EVENT: Record<string, IconName> = { opened: 'opened', changed: 'changed', done: 'done', withdrawn: 'withdrawn' };
const TASK_ACCENT: Record<string, string> = {
  opened: 'border-l-ok',
  changed: 'border-l-wait',
  done: 'border-l-ok',
  withdrawn: 'border-l-bad',
};

/** A task added, edited, done or deleted: a compact bar unlike a message, with the change in brief; a click opens the task in the Tasks tab. */
export function TaskLine({ i, titles, agent }: { i: TranscriptItem; titles?: Map<number, string>; agent?: string }) {
  const { apply } = useWorkspace();
  const ev = i.task_event?.event ?? 'changed';
  const row = taskRow(i, titles);
  const me = useStore().me;
  return (
    <button
      type="button"
      className={`card-task flex w-fit max-w-full cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-card border border-l-4 border-line bg-panel px-2.5 py-1 text-left text-body ${TASK_ACCENT[ev] ?? 'border-l-wait'}`}
      data-kind="task_event"
      data-event={ev}
      title={`Open T${row.task} in the Tasks tab`}
      onClick={() => apply((w) => openTask(w, row.task))}
    >
      <Icon name={TASK_EVENT[ev] ?? 'changed'} size={14} label={row.label} />
      <span className="card-event text-meta font-semibold uppercase tracking-wide">{row.label}</span>
      <span className="mono">T{row.task}</span>
      {row.who && row.who !== agent ? <span className="muted">by {isPerson(row.who, me) ? 'you' : row.who}</span> : null}
      {row.title ? <span className="max-w-[24ch] overflow-hidden text-ellipsis whitespace-nowrap font-medium">{row.title}</span> : null}
      {row.parts.length ? <TaskChanges parts={row.parts} /> : null}
      <span className="muted card-time">{clockTime(i.at)}</span>
    </button>
  );
}

/** What changed, one piece per change: the old value struck through, then the new one. */
function TaskChanges({ parts }: { parts: TaskChange[] }) {
  return (
    <span className="muted min-w-0 break-words">
      {parts.map((c, n) => (
        <span key={n}>
          {n ? ', ' : null}
          {'text' in c ? (
            c.text
          ) : (
            <>
              {c.field ? `${c.field} ` : null}
              <s>{c.old}</s> {c.now}
            </>
          )}
        </span>
      ))}
    </span>
  );
}

/** The task board's news of a task (`T<N> done by x`) as the same card a task event has, not a bubble from someone. */
export function TaskNote({ m, task, titles }: { m: ChatMessage; task: number; titles?: Map<number, string> }) {
  const { apply } = useWorkspace();
  const [, verb, rest] = /^T\d+\s+(\S+)\s*(.*)$/s.exec(m.body) ?? [];
  const ev = verb === 'done' ? 'done' : verb === 'opened' ? 'opened' : 'changed';
  return (
    <button
      type="button"
      className={`card-task flex w-fit max-w-full cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-card border border-l-4 border-line bg-panel px-2.5 py-1 text-left text-body ${TASK_ACCENT[ev]}`}
      data-kind="task_event"
      data-event={ev}
      title={`Open T${task} in the Tasks tab`}
      onClick={() => apply((w) => openTask(w, task))}
    >
      <Icon name={TASK_EVENT[ev]!} size={14} label={`Task ${verb ?? 'changed'}`} />
      <span className="card-event text-meta font-semibold uppercase tracking-wide">{verb ? `Task ${verb}` : 'Task'}</span>
      <span className="mono">T{task}</span>
      {titles?.get(task) ? (
        <span className="max-w-[24ch] overflow-hidden text-ellipsis whitespace-nowrap font-medium">{titles.get(task)}</span>
      ) : null}
      {rest ? <span className="muted min-w-0 break-words">{rest}</span> : null}
      <span className="muted card-time">{clockTime(m.at)}</span>
    </button>
  );
}
