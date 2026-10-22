// The devices paired with the daemon (docs/pc-app-gaps.md K6): when each was last seen, and Revoke behind a confirm. Nothing without devices.list.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { ago } from '@handline/app-core';
import { classify, sentence, useDevices, useOff, useServes, useWrite } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Button } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

export function PairedDevices() {
  const c = usePalette();
  const query = useDevices();
  const { served } = useServes('devices.list');
  const revoke = useWrite('device.revoke');
  const off = useOff('device.revoke');
  const [asking, setAsking] = useState<{ id: string; name: string } | null>(null);
  const rows = query.data?.devices ?? [];
  if (!served) return null;
  return (
    <View testID="paired-devices" style={{ gap: space.sm }}>
      <Text style={{ color: c.muted }}>Paired devices</Text>
      <Loadable query={query} served={served} isEmpty={rows.length === 0} skeleton="settings" label="paired devices" empty="No device is paired.">
        <View>
          {rows.map((d) => (
            <View key={d.id} testID={`device-${d.id}`} style={{ flexDirection: 'row', alignItems: 'center', gap: space.md, paddingVertical: space.sm, borderTopWidth: 1, borderTopColor: c.line }}>
              <View style={{ flex: 1 }}>
                <Text style={{ color: c.ink }}>{d.name}</Text>
                <Text style={{ color: c.muted, fontSize: font.size.label }}>{d.connected ? 'connected' : d.last_seen ? `seen ${ago(d.last_seen)} ago` : 'never seen'}</Text>
              </View>
              <Button testID={`revoke-${d.id}`} label="Revoke" disabled={!!off} onPress={() => setAsking({ id: d.id, name: d.name })} />
            </View>
          ))}
        </View>
      </Loadable>
      {revoke.error ? (
        <Text testID="revoke-error" accessibilityRole="alert" style={{ color: c.block, fontSize: font.size.label }}>
          not revoked: {sentence(classify(revoke.error))}
        </Text>
      ) : null}
      {asking ? (
        <Sheet
          id="revoke"
          label={`Revoke ${asking.name}?`}
          submit="Revoke"
          kind="no"
          onCancel={() => setAsking(null)}
          onSubmit={() => {
            revoke.mutate({ device: asking.id });
            setAsking(null);
          }}
        >
          <Text style={{ color: c.ink }}>{asking.name} loses its access to this daemon at once. To use it again it has to be paired anew.</Text>
        </Sheet>
      ) : null}
    </View>
  );
}
