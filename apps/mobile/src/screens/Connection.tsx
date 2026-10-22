// How the PC is reached (docs/pc-app-gaps.md L6), read only: the listener's address, encryption and fingerprint, the heavy slots and dev mode.
// Nothing without connection.get.
import { Text, View } from 'react-native';
import { useConnection, useServes } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';

function Row(props: { id: string; label: string; value: string; small?: boolean }) {
  const c = usePalette();
  return (
    <View testID={`conn-${props.id}`} style={{ gap: 2 }}>
      <Text style={{ color: c.muted, fontSize: font.size.label }}>{props.label}</Text>
      <Text selectable style={{ color: c.ink, fontSize: props.small ? font.size.label : undefined }}>
        {props.value}
      </Text>
    </View>
  );
}

export function Connection() {
  const c = usePalette();
  const query = useConnection();
  const { served } = useServes('connection.get');
  const info = query.data;
  if (!served) return null;
  const l = info?.listener;
  return (
    <View testID="connection" style={{ gap: space.sm }}>
      <Text style={{ color: c.ink, fontWeight: '700' }}>Connection</Text>
      <Loadable query={query} served={served} skeleton="settings" label="connection" empty="Nothing to show.">
        {info ? (
          <View style={{ gap: space.sm }}>
            {l ? (
              <>
                <Row id="address" label="Address" value={l.address} />
                <Row
                  id="encryption"
                  label="Encryption"
                  value={l.encryption === 'tls' ? 'Encrypted' : 'Tailscale (not encrypted by the daemon)'}
                />
                {l.fingerprint ? <Row id="fingerprint" label="Fingerprint" value={l.fingerprint} small /> : null}
                {l.tailscale_host ? <Row id="tailscale" label="Tailscale host" value={l.tailscale_host} /> : null}
              </>
            ) : (
              <Text testID="conn-no-listener" style={{ color: c.muted }}>
                This process has no network listener.
              </Text>
            )}
            <Row id="slots" label="Heavy slots" value={String(info.heavy_slots.slots)} />
            <Row id="dev" label="Dev mode" value={info.dev_mode ? 'On' : 'Off'} />
          </View>
        ) : null}
      </Loadable>
    </View>
  );
}
