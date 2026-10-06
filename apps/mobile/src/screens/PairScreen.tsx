import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { radius, space } from '@handline/tokens';
import { RpcError } from '@handline/client';
import { pair, type Pairing } from '../session.ts';
import { Button } from './parts.tsx';
import { usePalette } from '../theme.ts';

/** Pairing from a pasted `handline://pair?...` uri. Scanning the QR with the camera is a follow-up. */
export function PairScreen(props: { onPaired: (p: Pairing) => void }) {
  const c = usePalette();
  const [uri, setUri] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true); setError(null);
    try { props.onPaired(await pair(uri, 'phone')); }
    catch (e) { setError(e instanceof RpcError ? (e.reason ?? e.message) : (e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <View style={{ padding: space.lg, gap: space.md }}>
      <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700' }}>Pair with a daemon</Text>
      <Text style={{ color: c.muted }}>On the computer: Settings, Pair device. Paste the pairing link it shows.</Text>
      <TextInput testID="pair-uri" value={uri} onChangeText={setUri} autoCapitalize="none" autoCorrect={false} multiline placeholder="handline://pair?host=…&fp=…&code=…"
        placeholderTextColor={c.muted}
        style={{ color: c.ink, borderColor: c.line, borderWidth: 1, borderRadius: radius.md, padding: space.md, minHeight: 90 }} />
      {error && <Text style={{ color: c.block }} accessibilityRole="alert">{error}</Text>}
      <Button testID="pair-go" label={busy ? 'Pairing…' : 'Pair'} onPress={() => void go()} disabled={busy || !uri.trim()} kind="yes" />
    </View>
  );
}
