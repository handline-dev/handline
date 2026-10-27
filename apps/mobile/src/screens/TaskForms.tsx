// The phone's new-task and edit forms: the fields they share (who it is assigned to, title, body, time box, priority) and the two sheets that send them.
import { useState } from 'react';
import { taskRef } from '@handline/app-core';
import { Text, View } from 'react-native';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useAgents, useOff, useWrite } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { ImageBodyInput } from '#src/screens/ImageBodyInput.tsx';
import { Button, PageHeader, Pill } from '#src/screens/parts.tsx';
import { Input, Sheet } from '#src/screens/TaskActions.tsx';

const PRIORITIES = [
  [1, '1 high'],
  [2, '2'],
  [3, '3 low'],
] as const;

interface TaskDraft {
  owner: string;
  title: string;
  body: string;
  box: string;
  priority: 1 | 2 | 3;
}

/** The fields a new task and an edited one share: owner, title, body, time box, priority. `id` prefixes the test ids. */
function TaskFields(p: { id: 'nt' | 'et'; owners: string[]; draft: TaskDraft; change: (patch: Partial<TaskDraft>) => void }) {
  const c = usePalette();
  const { id, draft, change } = p;
  return (
    <>
      <Text style={{ color: c.muted, fontWeight: '600' }}>Assigned to</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {p.owners.map((a) => (
          <Pill role="radio" key={a} on={draft.owner === a} label={a} onPress={() => change({ owner: a })} testID={`${id}-owner-${a}`} />
        ))}
      </View>
      <Input testID={`${id}-title`} label="Title" value={draft.title} onChange={(title) => change({ title })} />
      <ImageBodyInput testID={`${id}-body`} label="Body" value={draft.body} onChange={(body) => change({ body })} />
      <Input testID={`${id}-box`} label="Time box" value={draft.box} onChange={(box) => change({ box })} />
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        {PRIORITIES.map(([n, l]) => (
          <Pill
            role="radio"
            key={n}
            on={draft.priority === n}
            label={l}
            onPress={() => change({ priority: n })}
            testID={`${id}-priority-${n}`}
          />
        ))}
      </View>
    </>
  );
}

/** New task: owner, title, body, time box, priority. The row shows in the list at once; a refusal leaves the text here with the reason. */
export function NewTask({ onClose }: { onClose: () => void }) {
  const c = usePalette();
  const ask = useWrite('task.new');
  const why = useOff('task.new');
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  const [draft, setDraft] = useState<TaskDraft>({ owner: '', title: '', body: '', box: '2h', priority: 2 });
  const change = (patch: Partial<TaskDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const [missing, setMissing] = useState('');
  const send = () => {
    const { owner, title, body, box, priority } = draft;
    if (!owner) return setMissing('Pick who it is for.');
    if (!title.trim()) return setMissing('A task needs a title.');
    setMissing('');
    ask.mutateAsync({ owner, title: title.trim(), body, time_box: box.trim() || '2h', priority }).then(onClose, () => {});
  };
  const said = missing || (ask.isError ? sentence(classify(ask.error)) : '');
  return (
    <View testID="new-task" style={{ gap: space.md, padding: space.lg }}>
      <PageHeader bare title="New task" />
      <TaskFields id="nt" owners={agents} draft={draft} change={change} />
      {said ? (
        <Text testID="nt-error" accessibilityRole="alert" style={{ color: c.block }}>
          {said}
        </Text>
      ) : null}
      {why && <Text style={{ color: c.muted }}>{why}</Text>}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button testID="nt-send" kind="yes" label="Ask" disabled={ask.isPending || !!why} onPress={send} style={{ flex: 1 }} />
        <Button testID="nt-cancel" label="Cancel" onPress={onClose} />
      </View>
    </View>
  );
}

/** Edit: only what changed is sent. The sheet stays open with the text when the daemon refuses. */
export function EditTask({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const agents = useAgents().data?.agents.map((a) => a.name) ?? [];
  const [draft, setDraft] = useState<TaskDraft>({
    owner: t.to,
    title: t.title,
    body: t.body ?? '',
    box: t.time_box ?? '',
    priority: t.priority ?? 2,
  });
  const change = (patch: Partial<TaskDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const [missing, setMissing] = useState('');
  const save = () => {
    const { owner, title, body, box, priority } = draft;
    if (!title.trim()) return setMissing('A task needs a title.');
    setMissing('');
    const change = {
      ...(title.trim() !== t.title ? { title: title.trim() } : {}),
      ...(body !== (t.body ?? '') ? { body } : {}),
      ...(owner !== t.to ? { owner } : {}),
      ...(box.trim() && box.trim() !== t.time_box ? { time_box: box.trim() } : {}),
      ...(priority !== (t.priority ?? 2) ? { priority } : {}),
    };
    if (!Object.keys(change).length) return onClose();
    edit.mutateAsync({ task: t.id, ...change }).then(onClose, () => {});
  };
  return (
    <Sheet
      label={`edit ${taskRef(t.id)}`}
      submit="Save"
      busy={edit.isPending}
      onCancel={onClose}
      onSubmit={save}
      error={missing || (edit.isError ? sentence(classify(edit.error)) : undefined)}
    >
      <TaskFields id="et" owners={[...new Set([t.to, ...agents])]} draft={draft} change={change} />
    </Sheet>
  );
}
