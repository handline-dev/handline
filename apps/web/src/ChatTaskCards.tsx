// The task cards of the conversation: one component per kind of event, each saying which task, what happened and what is left to do.
// A shape icon, the title and the T number (both open the task), the time at the right; then a line of what happened and at most two
// lines of what was said, with "more" to open the rest in place.
import { useState } from 'react';
import type { ChatMessage, TranscriptItem } from '@handline/client';
import { CLOSED, clockTime, isPerson, openTask, type TaskChange, taskRow } from '@handline/app-core';
import { useMe } from '@handline/app-react';
import { useServes, useTask, useWrite } from '@handline/app-data';
import { useWorkspace } from '#src/Workspace.tsx';
import { Button, Icon } from '#src/ui/index.ts';

type Kind = 'handed-to' | 'done' | 'handed-on' | 'small';

const LINK = 'cursor-pointer border-0 bg-transparent p-0 text-xs text-ink-2 underline disabled:opacity-50';
const CLAMP = '[display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:2] overflow-hidden';

/** What was said, two lines of it, and "more" when there is a rest. */
function Said({ text }: { text: string }) {
  const [all, setAll] = useState(false);
  const long = text.length > 140 || text.includes('\n');
  return (
    <p className={`m-0 min-w-0 whitespace-pre-line break-words text-md text-ink-2 ${all ? '' : CLAMP}`}>
      {text}
      {long && !all ? (
        <>
          {' '}
          <button type="button" className={`${LINK} text-acc-ink`} onClick={() => setAll(true)}>
            more
          </button>
        </>
      ) : null}
    </p>
  );
}

const ICON: Record<Kind, string> = { 'handed-to': 'inbox', done: 'done', 'handed-on': 'forward', small: 'opened' };

/** One card: the title and the T number open the task in the Tasks tab; Done is at the end of the line of a task that is the person's. */
function TaskCard(props: {
  ev: string;
  kind: Kind;
  label: string;
  task: number;
  title?: string | null;
  what: string;
  said?: string;
  at: string;
  icon?: string;
}) {
  const { apply } = useWorkspace();
  const me = useMe();
  const t = useTask(props.task).data;
  const done = useWrite('task.done');
  const { served } = useServes('task.done');
  const open = () => apply((w) => openTask(w, props.task));
  const canDone = served && !!t && !CLOSED.includes(t.state) && isPerson(t.to, me);
  const small = props.kind === 'small';
  const title = props.title ?? `T${props.task}`;
  return (
    <div
      className="card-task group flex w-full max-w-full items-start gap-2 py-0.5 text-md"
      data-kind="task_event"
      data-event={props.ev}
      data-shape={props.kind}
    >
      <span className="mt-[3px] flex-none text-ink-2">
        <Icon name={(props.icon ?? ICON[props.kind]) as 'done'} size={16} label={props.label} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex min-w-0 items-baseline gap-x-2">
          {small ? <span className="muted flex-none">{props.what}</span> : null}
          <button
            type="button"
            className="min-w-0 cursor-pointer truncate border-0 bg-transparent p-0 text-left font-semibold text-ink"
            title={`Open T${props.task} in the Tasks tab`}
            onClick={open}
          >
            {title}
          </button>
          <button type="button" className={`${LINK} flex-none`} title={`Open T${props.task} in the Tasks tab`} onClick={open}>
            {`T${props.task}`}
          </button>
          {small ? <span className="muted min-w-0 truncate">{props.said}</span> : null}
          <span className="muted card-time ml-auto flex-none whitespace-nowrap text-meta">{clockTime(props.at)}</span>
        </div>
        {small ? null : <span className="text-ink">{props.what}</span>}
        {small || !props.said ? null : <Said text={props.said} />}
        {canDone ? (
          <div className="mt-1">
            <Button
              label={`Done T${props.task}`}
              disabled={done.isPending}
              onClick={() => done.mutate({ task: props.task, evidence: 'Done', reason: true })}
            >
              Done
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

const VERB: Record<string, string> = { opened: 'added', changed: 'changed', done: 'done', withdrawn: 'deleted' };

/** What changed as one sentence: `priority 2 → 1`, or the plain words of a note. */
const sentence = (parts: TaskChange[]): string =>
  parts.map((c) => ('text' in c ? c.text : `${c.field ? `${c.field} ` : ''}${c.old} → ${c.now}`)).join(', ');

/** A change of owner: the daemon writes `assigned to X` (the new owner only); the rest of the change is the note. */
function handedOn(parts: TaskChange[]): { to: string; rest: string } | null {
  const hit = parts.find((c): c is { text: string } => 'text' in c && c.text.startsWith('assigned to '));
  return hit ? { to: hit.text.slice('assigned to '.length), rest: sentence(parts.filter((c) => c !== hit)) } : null;
}

/** A task added, edited, done or deleted; `agent` is the one whose chat it is (its own changes need no 'by'). */
export function TaskLine({ i, titles, agent }: { i: TranscriptItem; titles?: Map<number, string>; agent?: string }) {
  const ev = i.task_event?.event ?? 'changed';
  const row = taskRow(i, titles);
  const me = useMe();
  const who = row.who ? (isPerson(row.who, me) ? 'you' : row.who) : '';
  const by = who && row.who !== agent ? `by ${who}` : '';
  const base = { ev, label: row.label, task: row.task, title: row.title, at: i.at };
  if (ev === 'done')
    return <TaskCard {...base} kind="done" what={['Done', who ? `by ${who}` : ''].filter(Boolean).join(' ')} said={sentence(row.parts)} />;
  if (ev === 'opened' && row.who !== agent)
    return (
      <TaskCard
        {...base}
        kind="handed-to"
        what={`Handed to ${agent ?? 'this agent'}${who ? ` by ${who}` : ''}`}
        said={sentence(row.parts)}
      />
    );
  if (ev === 'changed') {
    const h = handedOn(row.parts);
    if (h) return <TaskCard {...base} kind="handed-on" what={`Handed on${who ? `: ${who} → ${h.to}` : ` to ${h.to}`}`} said={h.rest} />;
  }
  const word = ev === 'changed' ? '' : (VERB[ev] ?? 'changed');
  const icon = ev === 'withdrawn' ? 'dismiss' : ev === 'changed' ? 'edit' : 'opened';
  const said = ev === 'changed' ? [sentence(row.parts), by].filter(Boolean).join(' ') : [by, sentence(row.parts)].filter(Boolean).join(' · ');
  return <TaskCard {...base} kind="small" icon={icon} what={word ? `${word[0]!.toUpperCase()}${word.slice(1)}` : ''} said={said} />;
}

/** The task board's news of a task (`T<N> done by x`) as the same card a task event has, not a bubble from someone. */
export function TaskNote({ m, task, titles }: { m: ChatMessage; task: number; titles?: Map<number, string> }) {
  const [, verb, rest] = /^T\d+\s+(\S+)\s*(.*)$/s.exec(m.body) ?? [];
  const ev = verb === 'done' ? 'done' : verb === 'opened' ? 'opened' : 'changed';
  const base = { ev, label: `Task ${verb ?? 'changed'}`, task, title: titles?.get(task), at: m.at };
  if (ev === 'done') return <TaskCard {...base} kind="done" what="Done" said={rest} />;
  return <TaskCard {...base} kind="small" icon={ev === 'opened' ? 'opened' : 'edit'} what={verb ?? ''} said={rest} />;
}

/** A batch discard as one card: a line per task with its title and reason; the line opens the task, Reopen brings it back as a draft. */
export function BatchCard({ i }: { i: TranscriptItem }) {
  const { apply } = useWorkspace();
  const reopen = useWrite('task.reopen');
  const b = i.task_batch;
  if (!b) return null;
  return (
    <div className="card-batch group my-1 flex flex-col gap-1 text-sm" data-kind="task_batch">
      <div className="flex items-baseline gap-x-2">
        <Icon name="withdrawn" size={14} label="Discarded" />
        <span className="card-event text-meta font-semibold uppercase tracking-wide">Discarded {b.tasks.length}</span>
        <span className="muted card-time ml-auto hidden group-hover:inline group-focus-within:inline">{clockTime(i.at)}</span>
      </div>
      {b.tasks.map((t) => (
        <div key={t.task} className="flex items-baseline gap-x-2" data-task={t.task}>
          <button
            type="button"
            className="flex min-w-0 flex-1 cursor-pointer flex-wrap items-baseline gap-x-2 border-0 bg-transparent p-0 text-left text-md"
            title={`Open T${t.task} in the Tasks tab`}
            onClick={() => apply((w) => openTask(w, t.task))}
          >
            <span className="mono">T{t.task}</span>
            <span className="max-w-[28ch] overflow-hidden text-ellipsis whitespace-nowrap font-medium">{t.title}</span>
            <span className="muted min-w-0 break-words">{t.reason}</span>
          </button>
          <Button label={`Reopen T${t.task}`} onClick={() => reopen.mutate({ task: t.task, note: 'reopened from the batch card' })}>
            Reopen
          </Button>
        </div>
      ))}
    </div>
  );
}
