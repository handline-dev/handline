// Handing an open task to someone else: anyone may, from the assignee pill on the task. Same short list as the web popover.
import { useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { assignGroups } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useAgents, useOff, useTeam, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { BottomSheet } from '#src/screens/parts.tsx';

const SHOWN = 5;

/** A search and a short list (who is likely, Me, the owner now); a tap hands the task over at once and closes the sheet. */
export function AssignTask({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const c = usePalette();
  const me = useStore().me;
  const edit = useWrite('task.edit');
  const off = useOff('task.edit');
  const known = useAgents().data?.agents.map((a) => a.name) ?? [];
  const team = useTeam().data ?? { agents: [] };
  const groups = assignGroups(t, team, known, me);
  const likely = groups.filter((g) => g.key !== 'rest').flatMap((g) => g.names);
  const everyone = [...new Set([...groups.flatMap((g) => g.names), me, t.to])];
  const [find, setFind] = useState('');
  const q = find.trim().toLowerCase();
  const names = q
    ? everyone.filter((n) => n.toLowerCase().includes(q) || (n === me && 'me'.includes(q))).slice(0, 6)
    : [...new Set([...likely.slice(0, SHOWN - 2), me, t.to])];
  const pick = (to: string) => {
    if (off) return;
    if (to === t.to) return onClose();
    edit.mutateAsync({ task: t.id, owner: to }).then(onClose, () => {});
  };
  const error = off ?? (edit.isError ? sentence(classify(edit.error)) : undefined);
  return (
    <BottomSheet testID="sheet" padded onClose={onClose}>
      <TextInput
        testID="assign-find"
        accessibilityLabel="Find someone"
        placeholder="Find someone"
        placeholderTextColor={c.muted}
        value={find}
        onChangeText={setFind}
        autoCapitalize="none"
        autoCorrect={false}
        style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, padding: space.md, minHeight: 44 }}
      />
      {names.map((n) => (
        <Pressable
          key={n}
          testID={`assign-${n}`}
          accessibilityRole="button"
          onPress={() => pick(n)}
          style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', minHeight: 44, paddingHorizontal: space.sm }}
        >
          <Text numberOfLines={1} style={{ color: c.ink, flexShrink: 1 }}>
            {n === me ? 'Me' : n}
          </Text>
          {n === t.to ? <Text style={{ color: c.muted, fontSize: font.size.hint }}>now</Text> : null}
        </Pressable>
      ))}
      {!names.length ? <Text style={{ color: c.muted }}>{q ? 'No one matches.' : 'There is no one else to assign it to.'}</Text> : null}
      {!q && everyone.length > names.length ? (
        <Text style={{ color: c.muted, fontSize: font.size.hint }}>{`Type to find more · ${everyone.length} in all`}</Text>
      ) : null}
      {error ? (
        <Text testID="sheet-error" accessibilityRole="alert" style={{ color: c.block }}>
          {error}
        </Text>
      ) : null}
    </BottomSheet>
  );
}
