// The File tab of an agent's page: a .md or .json file in the agent's worktree, shown as text, edited in place and saved against the etag the read gave.
import { useState } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { shown, useFileEditor } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Markdown } from '#src/screens/Markdown.tsx';
import { Button } from '#src/screens/parts.tsx';

export function AgentFile({ agent }: { agent: string }) {
  const c = usePalette();
  const [typed, setTyped] = useState('');
  const [path, setPath] = useState('');
  const { q, served, saving, draft, setDraft, note, submit, edit, discard, editable } = useFileEditor(agent, path);
  const d = q.data;
  const open = () => setPath(typed.trim().replace(/^\.?\//, ''));
  const field = { color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, padding: space.md, minHeight: 44 } as const;
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
              {editable && <Button testID="file-edit" label="Edit" onPress={edit} />}
            </>
          ) : (
            <>
              <Button testID="file-save" label="Save" kind="yes" disabled={saving} onPress={() => void submit()} />
              <Button testID="file-discard" label="Discard" onPress={discard} />
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
              <TextInput testID="file-text" accessibilityLabel="File text" multiline value={draft} onChangeText={setDraft} autoCapitalize="none" autoCorrect={false} style={{ ...field, flex: 1, margin: space.md, fontFamily: 'Menlo', fontSize: font.size.label, textAlignVertical: 'top' }} />
            ) : (
              <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: space.md }}>
                {/\.md$/i.test(path) ? <Markdown text={d.text} /> : <Text style={{ color: c.ink, fontFamily: 'Menlo', fontSize: font.size.label }}>{shown(path, d.text)}</Text>}
                {d.truncated && <Text style={{ color: c.muted }}>The file is cut here (over 1 MiB); it cannot be edited.</Text>}
              </ScrollView>
            ))}
        </Loadable>
      )}
    </View>
  );
}
