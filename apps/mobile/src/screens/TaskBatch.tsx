// A verb on the tasks marked in the Tasks list, one or many, as on the web's row menu and bulk bar: pick the verb (each says why it cannot run),
// give what it needs (a reason, an owner, a priority or a time box), confirm with the count, then see what happened to each task.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { applyEach, plan, summary, taskRef, TASK_VERBS, type Outcome, type RowVerb } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { Task } from '@handline/client';
import { useAgents, useOff, useTaskActions } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { MenuItem, MenuSheet, Pill } from '#src/screens/parts.tsx';
import { Input, Sheet } from '#src/screens/TaskActions.tsx';

type Arg = 'reason' | 'reasonOptional' | 'owner' | 'priority' | 'timebox' | null;
const ARG: Record<RowVerb, Arg> = {
  done: 'reason',
  reopen: 'reasonOptional',
  queue: null,
  draft: null,
  release: 'reason',
  withdraw: null,
  cancel: 'reasonOptional',
  discard: 'reason',
  owner: 'owner',
  priority: 'priority',
  timebox: 'timebox',
};
const DANGER: RowVerb[] = ['cancel', 'discard', 'withdraw'];

/** Why the daemon cannot run a verb now, or null: the connection, then whether it serves the method. */
function useCans(): Record<RowVerb, string | undefined> {
  const edit = useOff('task.edit');
  return {
    done: useOff('task.done'),
    reopen: useOff('task.reopen'),
    queue: useOff('task.queue'),
    draft: useOff('task.draft'),
    release: useOff('task.release'),
    withdraw: useOff('task.withdraw'),
    cancel: useOff('task.cancel'),
    discard: useOff('task.discard'),
    owner: edit,
    priority: edit,
    timebox: edit,
  };
}

export function TaskBatch(props: { tasks: Task[]; onClose: () => void; onDone: () => void }) {
  const c = usePalette();
  const me = useStore().me;
  const { verbs: run } = useTaskActions();
  const cans = useCans();
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  const [verb, setVerb] = useState<RowVerb | null>(null);
  const [arg, setArg] = useState('');
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[] | null>(null);
  const n = props.tasks.length;
  const what = n === 1 ? taskRef(props.tasks[0]!.id) : `${n} tasks`;

  if (outcomes)
    return (
      <Sheet label={summary(outcomes)} onCancel={props.onDone}>
        {outcomes.map((o) => (
          <Text key={o.task} testID={`outcome-${o.task}`} style={{ color: o.ok ? c.muted : c.block }}>
            {`${taskRef(o.task)} ${o.ok ? 'changed' : `not changed: ${o.why}`}`}
          </Text>
        ))}
      </Sheet>
    );

  if (!verb)
    return (
      <MenuSheet testID="batch-menu" onClose={props.onClose}>
        {TASK_VERBS.map(({ verb: v, label }) => {
          const p = plan(v, props.tasks, me);
          const why = cans[v] ?? (p.run.length ? null : (p.skip[0]?.why ?? 'nothing to do'));
          return (
            <MenuItem
              key={v}
              testID={`batch-${v}`}
              label={label}
              disabled={!!why}
              danger={DANGER.includes(v)}
              note={why ?? (n > 1 ? `${p.run.length} of ${n}` : null)}
              onPress={() => (setArg(''), setMissing(false), setVerb(v))}
            />
          );
        })}
      </MenuSheet>
    );

  const kind = ARG[verb];
  const p = plan(verb, props.tasks, me);
  const label = TASK_VERBS.find((v) => v.verb === verb)!.label;
  const submit = async () => {
    if ((kind === 'reason' || kind === 'owner' || kind === 'priority' || kind === 'timebox') && !arg.trim()) return setMissing(true);
    setBusy(true);
    const ids = p.run.map((t) => t.id);
    const res = await applyEach(ids, (id) => run[verb](id, arg.trim()));
    setOutcomes([...res, ...p.skip.map((s) => ({ task: s.task.id, ok: false, why: s.why }))]);
    setBusy(false);
  };
  return (
    <Sheet
      label={`${label}: ${p.run.length === n ? what : `${p.run.length} of ${n} tasks`}?`}
      submit={label}
      kind={DANGER.includes(verb) ? 'no' : 'yes'}
      busy={busy}
      onCancel={() => setVerb(null)}
      onSubmit={() => void submit()}
      error={missing ? 'This needs an answer.' : undefined}
    >
      {kind === 'reason' || kind === 'reasonOptional' ? (
        <Input
          testID="batch-text"
          label={kind === 'reason' ? 'Reason' : 'Reason (optional)'}
          value={arg}
          invalid={missing}
          onChange={(v) => (setArg(v), setMissing(false))}
        />
      ) : null}
      {kind === 'timebox' ? (
        <Input
          testID="batch-text"
          label="Time box, like 2h"
          value={arg}
          invalid={missing}
          onChange={(v) => (setArg(v), setMissing(false))}
        />
      ) : null}
      {kind === 'priority' ? (
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          {(['1', '2', '3'] as const).map((x) => (
            <Pill key={x} testID={`batch-p${x}`} on={arg === x} label={`P${x}`} onPress={() => (setArg(x), setMissing(false))} />
          ))}
        </View>
      ) : null}
      {kind === 'owner' ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
          {agents.map((a) => (
            <Pill key={a} testID={`batch-owner-${a}`} on={arg === a} label={a} onPress={() => (setArg(a), setMissing(false))} />
          ))}
        </View>
      ) : null}
      {p.skip.length ? (
        <Text
          style={{ color: c.muted, fontSize: font.size.label }}
        >{`Skipped: ${p.skip.map((s) => `${taskRef(s.task.id)} (${s.why})`).join('; ')}`}</Text>
      ) : null}
    </Sheet>
  );
}
