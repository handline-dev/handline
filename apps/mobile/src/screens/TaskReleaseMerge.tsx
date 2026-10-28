// Release and Merge on the phone's task page (docs/pc-app-gaps.md R2). Release takes a doing task back to queued; its reason is optional; the
// contract's optional draft target is left out. Merge starts from this task and folds the chosen others into it. Both keep their sheet open with the
// reason when the daemon refuses.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { taskRef } from '@handline/app-core';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useTasks, useWrite } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Input, Sheet } from '#src/screens/TaskActions.tsx';
import { Pill } from '#src/screens/parts.tsx';

export function ReleaseTask({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const release = useWrite('task.release');
  const [reason, setReason] = useState('');
  const send = () => void release.mutateAsync({ task: t.id, ...(reason.trim() ? { reason: reason.trim() } : {}) }).then(onClose, () => {});
  return (
    <Sheet
      label={`release ${taskRef(t.id)} back to the queue? why (optional):`}
      submit="Release"
      kind="no"
      busy={release.isPending}
      onCancel={onClose}
      onSubmit={send}
      error={release.isError ? sentence(classify(release.error)) : undefined}
    >
      <Input testID="release-reason" label="Reason" value={reason} onChange={setReason} />
    </Sheet>
  );
}

export function MergeTasks({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const c = usePalette();
  const merge = useWrite('task.merge');
  const others = (useTasks({}).data?.items ?? []).filter((x) => x.id !== t.id && x.state !== 'done' && x.state !== 'discarded');
  const [picked, setPicked] = useState<ReadonlySet<number>>(new Set());
  const [empty, setEmpty] = useState(false);
  const toggle = (id: number) => {
    const next = new Set(picked);
    if (!next.delete(id)) next.add(id);
    setPicked(next);
    setEmpty(false);
  };
  const send = () => {
    if (!picked.size) return setEmpty(true);
    merge.mutateAsync({ into: t.id, tasks: [...picked] }).then(onClose, () => {});
  };
  return (
    <Sheet
      label={`fold other tasks into ${taskRef(t.id)}`}
      submit="Merge"
      kind="no"
      busy={merge.isPending}
      onCancel={onClose}
      onSubmit={send}
      error={empty ? 'Pick at least one task to fold in.' : merge.isError ? sentence(classify(merge.error)) : undefined}
    >
      {others.length === 0 ? <Text style={{ color: c.muted }}>No other open tasks.</Text> : null}
      <View style={{ gap: space.sm }}>
        {others.map((x) => (
          <Pill
            role="radio"
            key={x.id}
            on={picked.has(x.id)}
            label={`${taskRef(x.id)} ${x.title}`}
            onPress={() => toggle(x.id)}
            testID={`merge-${x.id}`}
          />
        ))}
      </View>
    </Sheet>
  );
}
