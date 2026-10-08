// The way into Team and HR from the app: a small button over the tabs (the dot and count are the proposals that wait), and a page with the
// two lists. It hides while an agent page is open.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useSelector } from '@handline/app-react';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Hr, useHrCount } from './Hr.tsx';
import { Team } from './Team.tsx';

export function TeamEntry() {
  const c = usePalette();
  const agentOpen = useSelector((s) => !!s.page?.agent);
  const count = useHrCount();
  const [page, setPage] = useState<null | 'team' | 'hr'>(null);
  if (page === null)
    return agentOpen ? null : (
      <Pressable
        testID="team-open"
        accessibilityRole="button"
        accessibilityLabel={count ? `Team, ${count} HR waiting` : 'Team'}
        onPress={() => setPage('team')}
        style={{
          position: 'absolute',
          right: space.lg,
          bottom: 72,
          borderWidth: 1,
          borderColor: c.line,
          borderRadius: radius.pill,
          backgroundColor: c.panel,
          paddingVertical: space.sm,
          paddingHorizontal: space.md,
        }}
      >
        <Text style={{ color: c.ink, fontWeight: '600' }}>{count ? `● Team ${count}` : 'Team'}</Text>
      </Pressable>
    );
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
    <View testID="team-page" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: c.bg }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: space.lg, paddingTop: space.md }}>
        <Pressable
          testID="team-back"
          accessibilityRole="button"
          accessibilityLabel="Back"
          onPress={() => setPage(null)}
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
