// The actions on one task or on many from the Tasks views: the menu of verbs (each gated by what the daemon serves and by what applies),
// the sheet that confirms with the count and asks for what the verb needs, and the sheet that says what happened to each task.
import { useRef, useState } from 'react';
import type { useBulk } from './taskActions.ts';
import { plan, TASK_VERBS, type Outcome, type RowVerb } from '@handline/app-core';
import type { Task } from '@handline/client';
import { Button, Input, NativeSelect, Popup, PopupRow, PROW, PROW_NAME } from './ui/index.ts';
import { Sheet, useCan } from './taskui.tsx';

export type Cans = Record<RowVerb, { ok: boolean; why: string | undefined }>;

/** Whether the daemon serves what each verb writes; the hooks are fixed in number, one per method. */
export function useVerbCans(): Cans {
  const edit = useCan('task.edit');
  const done = useCan('task.done');
  const reopen = useCan('task.reopen');
  const queue = useCan('task.queue');
  const draft = useCan('task.draft');
  const release = useCan('task.release');
  const withdraw = useCan('task.withdraw');
  const cancel = useCan('task.cancel');
  const discard = useCan('task.discard');
  return { done, reopen, queue, draft, release, withdraw, cancel, discard, owner: edit, priority: edit, timebox: edit };
}

const label = (verb: RowVerb) => TASK_VERBS.find((v) => v.verb === verb)!.label;

/** The verbs of a menu for these tasks: each with why it cannot be used (not served, or applies to none of them), else how many it runs on. */
export function verbChoices(tasks: Task[], me: string, cans: Cans) {
  return TASK_VERBS.map(({ verb }) => {
    const p = plan(verb, tasks, me);
    const why = !cans[verb].ok ? cans[verb].why : p.run.length ? undefined : (p.skip[0]?.why ?? 'nothing to do');
    return { verb, label: label(verb), count: p.run.length, why };
  });
}

/** A button that opens the verbs for `tasks` (one row's, or the marked rows'). */
export function VerbMenu(props: { tasks: Task[]; me: string; cans: Cans; onPick: (verb: RowVerb) => void; title?: string; up?: boolean }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLSpanElement>(null);
  const many = props.tasks.length > 1;
  // The list scrolls and clips, so the popup is fixed at the trigger's edge, not inside the row.
  const at = open ? trigger.current?.getBoundingClientRect() : undefined;
  return (
    <span className="relative inline-block" ref={trigger} onClick={(e) => e.stopPropagation()}>
      <Button
        icon="more"
        label={props.title ?? 'Actions'}
        size="sm"
        onClick={() => setOpen(!open)}
        aria-haspopup="dialog"
        aria-expanded={open}
        onPointerDown={(e) => e.stopPropagation()} // so the popup's click-away does not close it just before this click toggles it
      />
      <span className="fixed z-30" style={at ? { left: at.right, top: props.up ? at.top : at.bottom } : undefined}>
        <Popup
          open={open}
          onClose={() => setOpen(false)}
          title={many ? `${props.tasks.length} tasks` : `#${props.tasks[0]?.id}`}
          align="right"
          up={props.up}
        >
          {verbChoices(props.tasks, props.me, props.cans).map((c) =>
            c.why ? (
              <div key={c.verb} className={`${PROW} cursor-not-allowed opacity-50`} title={c.why} aria-disabled="true">
                <span className={PROW_NAME}>{c.label}</span>
              </div>
            ) : (
              <PopupRow
                key={c.verb}
                label={c.label}
                hint={many ? `${c.count} of ${props.tasks.length}` : undefined}
                onClick={() => (setOpen(false), props.onPick(c.verb))}
              />
            ),
          )}
        </Popup>
      </span>
    </span>
  );
}

const NEEDS: Record<RowVerb, 'text' | 'optional' | 'owner' | 'priority' | 'time' | null> = {
  done: 'text',
  discard: 'text',
  reopen: 'optional',
  cancel: 'optional',
  release: null,
  owner: 'owner',
  priority: 'priority',
  timebox: 'time',
  queue: null,
  draft: null,
  withdraw: null,
};
const PROMPT: Partial<Record<RowVerb, string>> = {
  done: 'what came of it?',
  discard: 'why?',
  reopen: 'why? (optional)',
  cancel: 'why? (optional)',
  timebox: 'time box (2h, 30m)',
};

/** The confirm of a verb: how many tasks it will run on (and which it skips and why), and the one thing it asks for. */
export function VerbSheet(props: {
  verb: RowVerb;
  tasks: Task[];
  me: string;
  agents: string[];
  onCancel: () => void;
  onRun: (verb: RowVerb, ids: number[], arg: string) => void;
}) {
  const { verb, tasks } = props;
  const { run, skip } = plan(verb, tasks, props.me);
  const need = NEEDS[verb];
  const [arg, setArg] = useState(need === 'priority' ? '2' : '');
  const [empty, setEmpty] = useState(false);
  const required = need === 'text' || need === 'owner' || need === 'time';
  const shown = run.slice(0, 8);
  const submit = () =>
    required && !arg.trim()
      ? setEmpty(true)
      : run.length &&
        props.onRun(
          verb,
          run.map((t) => t.id),
          arg.trim(),
        );
  return (
    <Sheet
      label={`${label(verb)} ${run.length} task${run.length === 1 ? '' : 's'}?`}
      submit={label(verb)}
      onCancel={props.onCancel}
      onSubmit={submit}
      busy={false}
    >
      <ul className="vlist m-0 basis-full list-none p-0">
        {shown.map((t) => (
          <li key={t.id}>
            <span className="mono">#{t.id}</span> {t.title}
          </li>
        ))}
        {run.length > shown.length ? <li className="muted">… and {run.length - shown.length} more</li> : null}
      </ul>
      {skip.length ? (
        <p className="vskip muted m-0 basis-full">
          {skip.length} skipped:{' '}
          {skip
            .slice(0, 3)
            .map((s) => s.why)
            .join('; ')}
          {skip.length > 3 ? '; …' : ''}
        </p>
      ) : null}
      {need === 'owner' ? (
        <NativeSelect value={arg} aria-label="New owner" onChange={(e) => (setArg(e.target.value), setEmpty(false))}>
          <option value="">pick an agent</option>
          {props.agents.map((a) => (
            <option key={a}>{a}</option>
          ))}
        </NativeSelect>
      ) : need === 'priority' ? (
        <NativeSelect value={arg} aria-label="Priority" onChange={(e) => setArg(e.target.value)}>
          <option value="1">1 high</option>
          <option value="2">2</option>
          <option value="3">3 low</option>
        </NativeSelect>
      ) : need ? (
        <Input
          grow
          className="tinput"
          value={arg}
          aria-label={PROMPT[verb]}
          placeholder={PROMPT[verb]}
          aria-invalid={empty && !arg.trim()}
          onChange={(e) => (setArg(e.target.value), setEmpty(false))}
        />
      ) : null}
      {empty ? (
        <span className="tsheet-err basis-full text-bad" role="alert">
          This is required.
        </span>
      ) : null}
    </Sheet>
  );
}

/** What happened to each task of a batch: the count, and for each that failed, which and why. */
export function ResultSheet(props: { what: string; outcomes: Outcome[]; titles: Map<number, string>; onClose: () => void }) {
  const bad = props.outcomes.filter((o) => !o.ok);
  const ok = props.outcomes.length - bad.length;
  return (
    <Sheet
      label={`${props.what}: ${ok} of ${props.outcomes.length} changed`}
      submit="Close"
      onCancel={props.onClose}
      onSubmit={props.onClose}
    >
      {bad.length ? (
        <ul className="vfailed m-0 basis-full list-none p-0 text-bad">
          {bad.map((o) => (
            <li key={o.task}>
              <span className="mono">#{o.task}</span> {props.titles.get(o.task) ?? ''} — {o.why}
            </li>
          ))}
        </ul>
      ) : (
        <span className="muted basis-full">Every task was changed.</span>
      )}
    </Sheet>
  );
}

/** The sheets the verbs open: the confirm for the verb asked, on the tasks `ids`, and what happened to the last batch. */
export function VerbSheets({ flow, ids }: { flow: ReturnType<typeof useVerbFlow>; ids: number[] }) {
  const { ask, setAsk, run, me, agents, all, bulk } = flow;
  return (
    <>
      {ask ? <VerbSheet verb={ask} tasks={flow.byId(ids)} me={me} agents={agents} onCancel={() => setAsk(null)} onRun={run} /> : null}
      {bulk.result ? (
        <ResultSheet
          what={bulk.result.what}
          outcomes={bulk.result.outcomes}
          titles={new Map(all.map((t) => [t.id, t.title]))}
          onClose={bulk.clearResult}
        />
      ) : null}
    </>
  );
}

/** Which verb is being asked about, and how it goes: a page with its own sheets for owner, priority and discard passes `own`; else every verb asks here. */
export function useVerbFlow(props: {
  all: Task[];
  me: string;
  agents: string[];
  bulk: ReturnType<typeof useBulk>;
  focus: () => void;
  own?: (verb: 'owner' | 'priority' | 'discard') => void;
}) {
  const [ask, setAsk] = useState<RowVerb | null>(null);
  return {
    ask,
    setAsk,
    all: props.all,
    me: props.me,
    agents: props.agents,
    bulk: props.bulk,
    byId: (ids: number[]) => ids.flatMap((i) => props.all.find((t) => t.id === i) ?? []),
    pick: (verb: RowVerb) =>
      props.own && (verb === 'owner' || verb === 'priority' || verb === 'discard') ? props.own(verb) : setAsk(verb),
    run: (verb: RowVerb, ids: number[], arg: string) => {
      setAsk(null);
      queueMicrotask(props.focus);
      void props.bulk.run(verb, ids, arg);
    },
  };
}
