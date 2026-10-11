import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { radius, space } from '@handline/tokens';
import { RpcError } from '@handline/client';
import { pair, type Pairing } from '../session.ts';
import { Button } from './parts.tsx';
import { ScanQr } from './ScanQr.tsx';
import { usePalette } from '../theme.ts';

/** Pairing from the QR the computer shows (camera), or from a pasted `handline://pair?...` uri. */
export function PairScreen(props: { onPaired: (p: Pairing) => void }) {
  const c = usePalette();
  const [uri, setUri] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const go = async (text = uri) => {
    setScanning(false);
    setBusy(true);
    setError(null);
    try {
      props.onPaired(await pair(text, 'phone'));
    } catch (e) {
      setError(e instanceof RpcError ? (e.reason ?? e.message) : (e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <View style={{ padding: space.lg, gap: space.md }}>
      <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700' }}>Pair with a daemon</Text>
      <Text style={{ color: c.muted }}>
        On the computer, choose Pair a phone in the Handline menu, and scan its code. Or run hl device pair and paste the link.
      </Text>
      {scanning ? (
        <ScanQr onScan={(t) => void go(t)} onCancel={() => setScanning(false)} />
      ) : (
        <Button testID="pair-scan" label="Scan the code" onPress={() => setScanning(true)} kind="yes" disabled={busy} />
      )}
      <TextInput
        testID="pair-uri"
        value={uri}
        onChangeText={setUri}
        autoCapitalize="none"
        autoCorrect={false}
        multiline
        placeholder="handline://pair?host=…&code=…"
        placeholderTextColor={c.muted}
        style={{ color: c.ink, borderColor: c.line, borderWidth: 1, borderRadius: radius.md, padding: space.md, minHeight: 90 }}
      />
      {error && (
        <Text style={{ color: c.block }} accessibilityRole="alert">
          {error}
        </Text>
      )}
      <Button testID="pair-go" label={busy ? 'Pairing…' : 'Pair'} onPress={() => void go(uri)} disabled={busy || !uri.trim()} kind="yes" />
    </View>
  );
}
