// The task cards of the conversation: a task event from the transcript and the task board's news of a task, each as one quiet line:
// a colour dot for how it went, the task's title, a grey line for what happened, and the time in a column on the right.
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { clockTime, isPerson, openTask, type TaskChange, taskRow } from '@handline/app-core';
import { useMe } from '@handline/app-react';
import { useWorkspace } from '#src/Workspace.tsx';

const DOT: Record<string, string> = { opened: 'bg-ok', changed: 'bg-wait', done: 'bg-ok', withdrawn: 'bg-bad' };
const VERB: Record<string, string> = { opened: 'added', changed: 'changed', done: 'done', withdrawn: 'deleted' };

/** One card: a click opens the task in the Tasks tab. */
function TaskCard(props: { ev: string; label: string; task: number; title?: string | null; what: string[]; at: string }) {
  const { apply } = useWorkspace();
  const what = props.what.filter(Boolean).join(' · ');
  return (
    <button
      type="button"
      className="card-task flex w-full cursor-pointer items-baseline gap-x-2 rounded-card border-0 bg-transparent px-1.5 py-1 text-left text-body tint"
      data-kind="task_event"
      data-event={props.ev}
      title={`Open T${props.task} in the Tasks tab`}
      onClick={() => apply((w) => openTask(w, props.task))}
    >
      <span role="img" aria-label={props.label} className={`size-2 flex-none self-center rounded-full ${DOT[props.ev] ?? 'bg-wait'}`} />
      {props.title ? (
        <span className="max-w-[16rem] flex-none overflow-hidden text-ellipsis whitespace-nowrap font-medium">{props.title}</span>
      ) : null}
      <span className="muted min-w-0 flex-1 truncate text-meta">{`T${props.task} · ${what}`}</span>
      <span className="muted card-time w-20 flex-none whitespace-nowrap text-right text-meta">{clockTime(props.at)}</span>
    </button>
  );
}

/** What changed as one sentence: `priority 2 → 1`, or the plain words of a note. */
const sentence = (parts: TaskChange[]): string =>
  parts.map((c) => ('text' in c ? c.text : `${c.field ? `${c.field} ` : ''}${c.old} → ${c.now}`)).join(', ');

/** A task added, edited, done or deleted; `agent` is the one whose chat it is (its own changes need no 'by'). */
export function TaskLine({ i, titles, agent }: { i: TranscriptItem; titles?: Map<number, string>; agent?: string }) {
  const ev = i.task_event?.event ?? 'changed';
  const row = taskRow(i, titles);
  const me = useMe();
  const by = row.who && row.who !== agent ? `by ${isPerson(row.who, me) ? 'you' : row.who}` : '';
  // A change says what it is on its own; added, done and deleted say the word and who did it.
  const what = ev === 'changed' ? [sentence(row.parts), by] : [[VERB[ev] ?? 'changed', by].filter(Boolean).join(' '), sentence(row.parts)];
  return <TaskCard ev={ev} label={row.label} task={row.task} title={row.title} what={what} at={i.at} />;
}

/** The task board's news of a task (`T<N> done by x`) as the same card a task event has, not a bubble from someone. */
export function TaskNote({ m, task, titles }: { m: ChatMessage; task: number; titles?: Map<number, string> }) {
  const [, verb, rest] = /^T\d+\s+(\S+)\s*(.*)$/s.exec(m.body) ?? [];
  const ev = verb === 'done' ? 'done' : verb === 'opened' ? 'opened' : 'changed';
  return (
    <TaskCard ev={ev} label={`Task ${verb ?? 'changed'}`} task={task} title={titles?.get(task)} what={[verb ?? '', rest ?? '']} at={m.at} />
  );
}
