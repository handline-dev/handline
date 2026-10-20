// Handing an open task to someone else: anyone may, from the assignee pill on the task.
import { Text, View } from 'react-native';
import { assignGroups, taskRef } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useAgents, useTeam, useWrite } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Pill } from '#src/screens/parts.tsx';
import { Sheet, useWhyNot } from '#src/screens/TaskActions.tsx';

/** Anyone can hand an open task to someone else: who it can go to, in the web's groups. */
export function AssignTask({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const c = usePalette();
  const me = useStore().me;
  const edit = useWrite('task.edit');
  const off = useWhyNot('task.edit');
  const known = useAgents().data?.agents.map((a) => a.name) ?? [];
  const team = useTeam().data ?? { agents: [] };
  const groups = assignGroups(t, team, known, me);
  return (
    <Sheet label={`assign ${taskRef(t.id)} to…`} onCancel={onClose} error={off ?? (edit.isError ? sentence(classify(edit.error)) : undefined)}>
      {groups.length ? (
        groups.map((g) => (
          <View key={g.key} style={{ gap: space.xs }}>
            <Text style={{ color: c.muted, fontSize: 12 }}>{g.label}</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
              {g.names.map((n) => (
                <Pill
                  key={n}
                  testID={`assign-${n}`}
                  on={false}
                  label={n}
                  onPress={() => void (!off && edit.mutateAsync({ task: t.id, owner: n }).then(onClose, () => {}))}
                />
              ))}
            </View>
          </View>
        ))
      ) : (
        <Text style={{ color: c.muted }}>There is no one else to assign it to.</Text>
      )}
    </Sheet>
  );
}
