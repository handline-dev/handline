// The Preview tab of an agent's page: the agent's own app (a dev server) in a web view. The address is the one the phone can reach.
import { useRef, useState } from 'react';
import { Linking, Text, TextInput, View } from 'react-native';
import { captureRef } from 'react-native-view-shot';
import { WebView } from 'react-native-webview';
import { markText } from '@handline/app-core';
import { classify, sentence, useChatActions, useUploadImage } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { MarkPicture, type Marked } from './MarkPicture.tsx';
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

export function AgentPreview({ agent }: { agent: string }) {
  const c = usePalette();
  const chat = useChatActions(agent);
  const upload = useUploadImage();
  const page = useRef<View>(null);
  const [shot, setShot] = useState<string | null>(null);
  const [problem, setProblem] = useState('');
  const [typed, setTyped] = useState('');
  const [url, setUrl] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const go = () => {
    const a = addressOf(typed);
    if (a === url) setReload((n) => n + 1);
    setUrl(a);
  };
  // "Mark an area to ask": the page as drawn now is marked up, and the plain and the marked picture go to the agent's chat with the memos as lines.
  const grab = () =>
    captureRef(page, { format: 'png', result: 'tmpfile' }).then(setShot, (e: unknown) => setProblem(sentence(classify(e))));
  const ask = async (r: Marked | null) => {
    const uri = shot;
    setShot(null);
    if (!r || !uri || !r.marks.some((m) => m.text.trim())) return;
    try {
      const plain = await upload(await (await fetch(uri)).blob(), 'preview.png', 'image/png');
      const drawn = r.blob.size ? await upload(r.blob, 'preview marked.jpg', 'image/jpeg') : null;
      chat.send(markText('', r.marks), { attachments: drawn ? [plain.id, drawn.id] : [plain.id] });
    } catch (e) {
      setProblem(sentence(classify(e)));
    }
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
      {url && (
        <View style={{ flexDirection: 'row', gap: space.sm, paddingHorizontal: space.md, paddingBottom: space.sm }}>
          <Button testID="preview-mark" label="Mark an area" onPress={() => void grab()} />
          <Button testID="preview-outside" label="Open outside" onPress={() => void Linking.openURL(url)} />
        </View>
      )}
      {problem !== '' && <Text style={{ color: c.block, paddingHorizontal: space.md }}>{problem}</Text>}
      {shot && <MarkPicture uri={shot} onCancel={() => setShot(null)} onDone={(r) => void ask(r)} />}
      {url ? (
        <View ref={page} collapsable={false} style={{ flex: 1 }}>
          <WebView key={reload} testID="preview-web" source={{ uri: url }} style={{ flex: 1 }} />
        </View>
      ) : (
        <Text style={{ color: c.muted, padding: space.md }}>Type the address of the agent's app: https, or http on your tailnet (.ts.net).</Text>
      )}
    </View>
  );
}
