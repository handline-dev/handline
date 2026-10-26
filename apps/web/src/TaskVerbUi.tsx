// The actions on one task or on many from the Tasks views: the menu of verbs (each gated by what the daemon serves and by what applies),
// the sheet that confirms with the count and asks for what the verb needs, and the sheet that says what happened to each task.
import { useRef, useState, type ReactElement, type ReactNode } from 'react';
import type { useBulk } from '#src/taskActions.ts';
import { plan, taskRef, TASK_VERBS, type Outcome, type RowVerb } from '@handline/app-core';
import type { Task } from '@handline/client';
import { Button, Input, Menu, MenuButton, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, NativeSelect, type At } from '#src/ui/index.ts';
import type { IconName } from '@handline/app-ui';
import { Sheet, useCan } from '#src/taskui.tsx';
import { AnimatedList } from '#src/AnimatedList.tsx';

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

/** The menu's groups, in the order a person reaches for them: finish it, move it, change it, stop it (the stopping verbs last, in red). */
const GROUPS: { verbs: RowVerb[]; danger?: boolean }[] = [
  { verbs: ['done'] },
  { verbs: ['queue', 'draft', 'release', 'reopen'] },
  { verbs: ['owner', 'priority', 'timebox'] },
  { verbs: ['withdraw', 'cancel', 'discard'], danger: true },
];
const VERB_ICON: Record<RowVerb, IconName> = {
  done: 'done',
  queue: 'queued',
  draft: 'edit',
  release: 'undo',
  reopen: 'refresh',
  owner: 'owner',
  priority: 'priority',
  timebox: 'waiting',
  withdraw: 'withdrawn',
  cancel: 'cancel',
  discard: 'close',
};

/** The items of a task menu: what is said of the task first, then only the verbs that can run now, in groups with a line between; for one task
 *  the current owner and priority sit at the right of the words that change them. */
export function VerbItems(props: { tasks: Task[]; me: string; cans: Cans; onPick: (verb: RowVerb) => void }) {
  const { tasks } = props;
  const many = tasks.length > 1;
  // A verb that applies to none of the tasks is left out; one the daemon does not serve stays, dimmed, with the reason.
  const live = new Map(
    TASK_VERBS.flatMap(({ verb }) => {
      const run = plan(verb, tasks, props.me).run.length;
      return run ? [[verb, { verb, label: label(verb), count: run, why: props.cans[verb].ok ? undefined : props.cans[verb].why }] as const] : [];
    }),
  );
  const groups = GROUPS.map((g) => ({ ...g, items: g.verbs.flatMap((v) => live.get(v) ?? []) })).filter((g) => g.items.length);
  const now = (verb: RowVerb) =>
    many || !tasks[0] ? null : verb === 'owner' ? tasks[0].to : verb === 'priority' ? `P${tasks[0].priority ?? 2}` : null;
  return (
    <>
      <MenuLabel>{many ? `${tasks.length} tasks` : tasks[0] ? `${taskRef(tasks[0].id)} ${tasks[0].title}` : ''}</MenuLabel>
      {groups.length ? (
        groups.map((g, i) => (
          <div key={g.verbs[0]} role="group">
            {i ? <MenuSeparator /> : null}
            {g.items.map((c) => (
              <MenuItem key={c.verb} icon={VERB_ICON[c.verb]} danger={g.danger} disabled={!!c.why} title={c.why} onSelect={() => props.onPick(c.verb)}>
                {c.label}
                {many ? (
                  <span className="muted ml-auto text-hint">{`${c.count} of ${tasks.length}`}</span>
                ) : now(c.verb) ? (
                  <span className="muted ml-auto pl-3 text-hint">{now(c.verb)}</span>
                ) : null}
              </MenuItem>
            ))}
          </div>
        ))
      ) : (
        <MenuItem disabled>Nothing to do here</MenuItem>
      )}
    </>
  );
}

/** A button that opens the verbs for `tasks` (one row's, or the marked rows'). */
export function VerbMenu(props: {
  tasks: Task[];
  me: string;
  cans: Cans;
  onPick: (verb: RowVerb) => void;
  title?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  word?: string;
}) {
  const many = props.tasks.length > 1;
  return (
    <span className="inline-block" onClick={(e) => e.stopPropagation()}>
      <MenuButton
        label={props.title ?? 'Actions'}
        icon={props.word ? undefined : 'ellipsis'}
        trigger={props.word}
        size="sm"
        open={props.open}
        onOpenChange={props.onOpenChange}
        name={many ? `${props.tasks.length} tasks` : `#${props.tasks[0]?.id}`}
      >
        <VerbItems tasks={props.tasks} me={props.me} cans={props.cans} onPick={props.onPick} />
      </MenuButton>
    </span>
  );
}

/** A list row's menu: a right-click on the row opens it at the cursor, the ⋯ button handed to `children` opens it under the button.
 *  A press outside closes it, so a second row's menu opening closes the first. */
export function RowMenu(props: {
  tasks: Task[];
  me: string;
  cans: Cans;
  onPick: (verb: RowVerb) => void;
  children: (more: ReactNode, open: boolean) => ReactElement<{ onContextMenu?: (e: React.MouseEvent) => void }>;
}) {
  const at = useRef<((p: At) => void) | null>(null);
  const [open, setOpen] = useState(false);
  const wasOpen = useRef(false);
  const more = (
    <Button
      icon="ellipsis"
      size="sm"
      label="Actions"
      aria-haspopup="menu"
      aria-expanded={open}
      onPointerDownCapture={() => (wasOpen.current = open)}
      onClick={(e) => {
        e.stopPropagation();
        const r = e.currentTarget.getBoundingClientRect();
        if (!wasOpen.current) at.current?.({ x: r.left, y: r.bottom, w: r.width });
        wasOpen.current = false;
      }}
    />
  );
  return (
    <Menu openRef={at} onOpenChange={setOpen}>
      <MenuTrigger>{props.children(more, open)}</MenuTrigger>
      <MenuContent aria-label={`#${props.tasks[0]?.id}`} onClick={(e) => e.stopPropagation()}>
        <VerbItems tasks={props.tasks} me={props.me} cans={props.cans} onPick={props.onPick} />
      </MenuContent>
    </Menu>
  );
}

const NEEDS: Record<RowVerb, 'text' | 'optional' | 'owner' | 'priority' | 'time' | null> = {
  done: 'text',
  discard: 'text',
  reopen: 'optional',
  cancel: 'optional',
  release: 'text',
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
  release: 'why?',
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
            <span className="mono">{taskRef(t.id)}</span> {t.title}
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
      <AnimatedList>{ask ? <VerbSheet key="VerbSheet" verb={ask} tasks={flow.byId(ids)} me={me} agents={agents} onCancel={() => setAsk(null)} onRun={run} /> : null}</AnimatedList>
      <AnimatedList>
      {bulk.result ? (
        <ResultSheet key="result"
          what={bulk.result.what}
          outcomes={bulk.result.outcomes}
          titles={new Map(all.map((t) => [t.id, t.title]))}
          onClose={bulk.clearResult}
        />
      ) : null}
      </AnimatedList>
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
