// Team and HR from the Agents header: a button (the dot and count are the proposals that wait) and a page with the two lists.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Hr, useHrCount } from './Hr.tsx';
import { Button } from './parts.tsx';
import { Team } from './Team.tsx';

export function TeamButton(props: { onPress: () => void }) {
  const count = useHrCount();
  return (
    <Button
      testID="team-open"
      label={count ? `● Team ${count}` : 'Team'}
      accessibilityLabel={count ? `Team, ${count} HR waiting` : 'Team'}
      onPress={props.onPress}
      style={{ paddingVertical: space.sm }}
    />
  );
}

export function TeamPage(props: { onClose: () => void }) {
  const c = usePalette();
  const count = useHrCount();
  const [page, setPage] = useState<'team' | 'hr'>('team');
  const tab = (id: 'team' | 'hr', label: string) => (
    <Pressable
      key={id}
      testID={`team-tab-${id}`}
      accessibilityRole="tab"
      accessibilityState={{ selected: page === id }}
      onPress={() => setPage(id)}
      style={{
        flex: 1,
        alignItems: 'center',
        padding: space.md,
        borderBottomWidth: 2,
        borderBottomColor: page === id ? c.ink : 'transparent',
      }}
    >
      <Text style={{ color: page === id ? c.ink : c.muted, fontWeight: page === id ? '700' : '400' }}>{label}</Text>
    </Pressable>
  );
  return (
    <View testID="team-page" style={{ flex: 1, backgroundColor: c.bg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.lg, paddingTop: space.md }}>
        <Pressable
          testID="team-back"
          accessibilityRole="button"
          accessibilityLabel="Back"
          onPress={props.onClose}
          style={{ paddingVertical: space.sm, paddingRight: space.lg }}
        >
          <Text style={{ color: c.ink, fontWeight: '600' }}>‹ Back</Text>
        </Pressable>
      </View>
      <View accessibilityRole="tablist" style={{ flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: c.line }}>
        {tab('team', 'Team')}
        {tab('hr', count ? `● HR ${count}` : 'HR')}
      </View>
      <View style={{ flex: 1 }}>{page === 'hr' ? <Hr /> : <Team />}</View>
    </View>
  );
}
