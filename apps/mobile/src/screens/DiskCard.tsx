// The machine's disk need in My turn: the daemon's own words on what is too full, opened by a press.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { ago } from '@handline/app-core';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';

export function DiskCard(props: { detail: string; since: number }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  return (
    <View testID="disk-card" style={{ borderTopWidth: 1, borderTopColor: c.line }}>
      <Pressable
        testID="disk-open"
        onPress={() => setOpen(!open)}
        accessibilityLabel="Machine: Free disk space"
        style={{ flexDirection: 'row', gap: space.md, padding: space.lg, minHeight: 56, alignItems: 'center' }}
      >
        <Icon name="warn" decorative size={18} color={c.wait} />
        <View style={{ flex: 1 }}>
          <Text numberOfLines={open ? undefined : 1} style={{ color: c.ink, fontWeight: '700' }}>
            Free disk space
          </Text>
          <Text style={{ color: c.muted, fontSize: font.size.sm }}>Machine</Text>
        </View>
        <Text style={{ color: c.muted, fontSize: font.size.sm }}>{props.since ? ago(new Date(props.since).toISOString()) : ''}</Text>
      </Pressable>
      {open && props.detail ? (
        <View style={{ paddingHorizontal: space.lg, paddingBottom: space.lg }}>
          <Text style={{ color: c.ink }}>{props.detail}</Text>
        </View>
      ) : null}
    </View>
  );
}
