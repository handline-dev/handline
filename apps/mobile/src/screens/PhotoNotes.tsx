// The notes under a photo being marked (a numbered line to write for each mark) and the question that guards unsent ones.
import type { Dispatch } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { MARK_COLOR, type Mark, type MarksAction } from '@handline/app-core';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button, Link } from '#src/screens/parts.tsx';

/** The viewer's own ink: it sits on a dark scrim in either theme. */
export const INK = '#fff';
export const DIM = 'rgba(255,255,255,0.7)';

export const plural = (n: number) => `${n} ${n === 1 ? 'note' : 'notes'}`;

export function Notes(props: { marks: Mark[]; open: number | null; dispatch: Dispatch<MarksAction> }) {
  const { marks, open, dispatch } = props;
  if (!marks.length) return null;
  return (
    <ScrollView
      testID="notes"
      style={{ maxHeight: 200, flexGrow: 0 }}
      contentContainerStyle={{ gap: space.sm, padding: space.lg }}
      keyboardShouldPersistTaps="handled"
    >
      {marks.map((m, i) => (
        <View key={m.id} style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <View
            style={{ width: 24, height: 24, borderRadius: 12, backgroundColor: MARK_COLOR, alignItems: 'center', justifyContent: 'center' }}
          >
            <Text style={{ color: INK, fontSize: font.size.sm, fontWeight: '700' }}>{i + 1}</Text>
          </View>
          <TextInput
            testID={`note-${m.id}`}
            accessibilityLabel={`Note ${i + 1}`}
            autoFocus={m.id === open}
            multiline
            value={m.text}
            onFocus={() => dispatch({ type: 'open', id: m.id })}
            onChangeText={(text) => dispatch({ type: 'memo', id: m.id, text })}
            placeholder="What should change here?"
            placeholderTextColor={DIM}
            style={{
              flex: 1,
              color: INK,
              borderWidth: 1,
              borderColor: m.id === open ? MARK_COLOR : 'rgba(255,255,255,0.25)',
              borderRadius: radius.m,
              paddingHorizontal: space.md,
              paddingVertical: space.sm,
              maxHeight: 80,
            }}
          />
          <Link testID={`note-remove-${m.id}`} label="Remove" onPress={() => dispatch({ type: 'remove', id: m.id })} />
        </View>
      ))}
    </ScrollView>
  );
}

export function DiscardAsk(props: { count: number; onDiscard: () => void; onKeep: () => void }) {
  const c = usePalette();
  return (
    <View
      testID="discard"
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: 0,
        bottom: 0,
        backgroundColor: 'rgba(0,0,0,0.6)',
        justifyContent: 'center',
        padding: space.xl,
      }}
    >
      <View style={{ backgroundColor: c.panel, borderRadius: radius.m, padding: space.xl, gap: space.md }}>
        <Text style={{ color: c.ink, fontSize: font.size.lg, fontWeight: '700' }}>Discard {plural(props.count)}?</Text>
        <Text style={{ color: c.muted }}>They are not sent yet.</Text>
        <Button testID="discard-yes" kind="no" label="Discard" onPress={props.onDiscard} />
        <Button testID="discard-keep" label="Keep marking" onPress={props.onKeep} />
      </View>
    </View>
  );
}
