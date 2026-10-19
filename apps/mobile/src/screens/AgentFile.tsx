// The File tab of an agent's page: a .md or .json file in the agent's worktree, shown as text, edited in place and saved against the etag the read gave.
import { useEffect, useState } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { classify, sentence, useFile, useServes, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Markdown } from './Markdown.tsx';
import { Button } from './parts.tsx';

const EDITABLE = /\.(md|json)$/i;

/** JSON is shown indented; text that does not parse is shown as it is. */
function shown(path: string, text: string): string {
  if (!/\.json$/i.test(path)) return text;
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function AgentFile({ agent }: { agent: string }) {
  const c = usePalette();
  const [typed, setTyped] = useState('');
  const [path, setPath] = useState('');
  const q = useFile(agent, path);
  const { served } = useServes('file.read');
  const save = useWrite('file.write', `${agent}:${path}`);
  const [draft, setDraft] = useState<string | null>(null);
  const [etag, setEtag] = useState('');
  const [note, setNote] = useState('');
  const d = q.data;
  useEffect(() => {
    setDraft(null);
    setNote('');
  }, [path]);
  useEffect(() => {
    if (d) setEtag(d.etag);
  }, [d]);
  const open = () => setPath(typed.trim().replace(/^\.?\//, ''));
  const submit = async () => {
    if (draft === null) return;
    try {
      const r = await save.mutateAsync({ agent, path, text: draft, etag });
      setEtag(r.etag);
      setDraft(null);
      setNote('');
      void q.refetch();
    } catch (e) {
      const f = classify(e);
      setNote(f.kind === 'refused' && f.gate === 'stale' ? 'The file changed since you opened it. Reload it, then make the edit again.' : sentence(f));
    }
  };
  const editable = !!d && !d.truncated && EDITABLE.test(path);
  const field = { color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 44 } as const;
  return (
    <View testID="file-tab" style={{ flex: 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, padding: space.md }}>
        <TextInput
          testID="file-path"
          accessibilityLabel="File path"
          placeholder="docs/notes.md"
          placeholderTextColor={c.muted}
          value={typed}
          onChangeText={setTyped}
          onSubmitEditing={open}
          autoCapitalize="none"
          autoCorrect={false}
          style={{ ...field, flex: 1 }}
        />
        <Button testID="file-open" label="Open" onPress={open} />
      </View>
      {path !== '' && (
        <View style={{ flexDirection: 'row', gap: space.sm, paddingHorizontal: space.md, paddingBottom: space.sm }}>
          {draft === null ? (
            <>
              <Button testID="file-reload" label="Reload" onPress={() => void q.refetch()} />
              {editable && <Button testID="file-edit" label="Edit" onPress={() => setDraft(shown(path, d.text))} />}
            </>
          ) : (
            <>
              <Button testID="file-save" label="Save" kind="yes" disabled={save.isPending} onPress={() => void submit()} />
              <Button testID="file-discard" label="Discard" onPress={() => (setDraft(null), setNote(''))} />
            </>
          )}
        </View>
      )}
      {note !== '' && <Text style={{ color: c.block, paddingHorizontal: space.md }}>{note}</Text>}
      {path === '' ? (
        <Text style={{ color: c.muted, padding: space.md }}>Type the path of a Markdown or JSON file in the worktree.</Text>
      ) : (
        <Loadable query={q} served={served} skeleton="tasks" label="the file" empty="The file is empty.">
          {d &&
            (draft !== null ? (
              <TextInput testID="file-text" accessibilityLabel="File text" multiline value={draft} onChangeText={setDraft} autoCapitalize="none" autoCorrect={false} style={{ ...field, flex: 1, margin: space.md, fontFamily: 'Menlo', fontSize: 12, textAlignVertical: 'top' }} />
            ) : (
              <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: space.md }}>
                {/\.md$/i.test(path) ? <Markdown text={d.text} /> : <Text style={{ color: c.ink, fontFamily: 'Menlo', fontSize: 12 }}>{shown(path, d.text)}</Text>}
                {d.truncated && <Text style={{ color: c.muted }}>The file is cut here (over 1 MiB); it cannot be edited.</Text>}
              </ScrollView>
            ))}
        </Loadable>
      )}
    </View>
  );
}
