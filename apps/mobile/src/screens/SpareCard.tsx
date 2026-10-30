// The weekly-loss offer as a card of the Requests tab: the sentence, the first spare tasks, and a button that starts them all.
import { Pressable, Text, View } from 'react-native';
import { SPARE_SHOWN, spareNotice, type SpareNotice } from '@handline/app-core';
import { useTasks, useWrite } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { Button } from '#src/screens/parts.tsx';

/** The weekly-loss offers the daemon has open for the person, as task rows. */
export function useSpareNotices(): SpareNotice[] {
  const items = useTasks({}).data?.items;
  return (items ?? []).flatMap((t) => spareNotice(t) ?? []);
}

/** An account will lose weekly use: the sentence, the first spare tasks, and a button that starts them all. It stays until Start or the X. */
export function SpareCard(props: { n: SpareNotice; onTask: (id: number) => void }) {
  const c = usePalette();
  const { n } = props;
  const rows = new Map((useTasks({}).data?.items ?? []).map((t) => [t.id, t]));
  const answer = useWrite('task.done');
  const reply = (evidence: string) => answer.mutate({ task: n.task, evidence, reason: true });
  const small = { color: c.muted, fontSize: font.size.sm } as const;
  return (
    <View testID="notice-spare" style={{ flexDirection: 'row', gap: space.sm, padding: space.md, backgroundColor: c.raised }}>
      <Icon name="waiting" decorative size={16} color={c.wait} />
      <View style={{ flex: 1, gap: space.sm }}>
        <Pressable accessibilityRole="button" onPress={() => props.onTask(n.task)}>
          <Text style={{ color: c.ink }}>{n.text}</Text>
        </Pressable>
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
        {n.ids.length ? (
          <Button
            kind="yes"
            icon="play"
            label={n.ids.length === 1 ? 'Start 1 task' : `Start ${n.ids.length} tasks`}
            disabled={answer.isPending}
            onPress={() => reply('start')}
          />
        ) : null}
      </View>
      <Button
        testID="spare-close"
        icon="close"
        label=""
        accessibilityLabel="Close this notice"
        onPress={() => reply('dismiss')}
        style={{ alignSelf: 'flex-start' }}
      />
    </View>
  );
}
