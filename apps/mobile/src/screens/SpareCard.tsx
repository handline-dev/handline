// The weekly-loss offer inside its Requests item: the sentence and the first spare tasks. Start and Dismiss are the item's answers.
import { Text, View } from 'react-native';
import { SPARE_SHOWN, type SpareNotice } from '@handline/app-core';
import { useTasks } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

export function SpareTasks({ n }: { n: SpareNotice }) {
  const c = usePalette();
  const rows = new Map((useTasks({}).data?.items ?? []).map((t) => [t.id, t]));
  const small = { color: c.muted, fontSize: font.size.sm } as const;
  return (
    <View testID="notice-spare" style={{ gap: space.sm }}>
      <Text style={{ color: c.ink }}>{n.text}</Text>
      <View style={{ gap: 2 }}>
        {n.ids.slice(0, SPARE_SHOWN).map((id) => (
          <View key={id} style={{ flexDirection: 'row', gap: space.xs }}>
            <Text style={{ ...small, color: c.ink, fontFamily: font.mono, fontWeight: '700' }}>{`T${id}`}</Text>
            <Text numberOfLines={1} style={{ ...small, flex: 1 }}>
              {rows.get(id)?.title ?? ''}
            </Text>
            {rows.get(id) ? <Text style={small}>{rows.get(id)!.to.split('.').at(-1)}</Text> : null}
          </View>
        ))}
        {n.ids.length > SPARE_SHOWN ? <Text style={small}>{`and ${n.ids.length - SPARE_SHOWN} more`}</Text> : null}
      </View>
    </View>
  );
}
