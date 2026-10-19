// The Preview tab of an agent's page: the agent's own app (a dev server) in a web view. The address is the one the phone can reach.
import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { WebView } from 'react-native-webview';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';

/** What was typed as an address: a bare host is https, except on the tailnet (.ts.net), the one place the phone allows plain http. Null when it cannot be one. */
export function addressOf(typed: string): string | null {
  const t = typed.trim();
  if (!t) return null;
  const url = /^https?:\/\//i.test(t) ? t : `${/^[^/:]+\.ts\.net(:|\/|$)/i.test(t) ? 'http' : 'https'}://${t}`;
  try {
    return new URL(url).host ? url : null;
  } catch {
    return null;
  }
}

export function AgentPreview() {
  const c = usePalette();
  const [typed, setTyped] = useState('');
  const [url, setUrl] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const go = () => {
    const a = addressOf(typed);
    if (a === url) setReload((n) => n + 1);
    setUrl(a);
  };
  return (
    <View testID="preview-tab" style={{ flex: 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, padding: space.md }}>
        <TextInput
          testID="preview-address"
          accessibilityLabel="Address"
          placeholder="my-mac.ts.net:5173"
          placeholderTextColor={c.muted}
          value={typed}
          onChangeText={setTyped}
          onSubmitEditing={go}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          style={{ flex: 1, color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 44 }}
        />
        <Button testID="preview-go" label={url ? 'Reload' : 'Show'} onPress={go} />
      </View>
      {url ? (
        <WebView key={reload} testID="preview-web" source={{ uri: url }} style={{ flex: 1 }} />
      ) : (
        <Text style={{ color: c.muted, padding: space.md }}>Type the address of the agent's app: https, or http on your tailnet (.ts.net).</Text>
      )}
    </View>
  );
}
