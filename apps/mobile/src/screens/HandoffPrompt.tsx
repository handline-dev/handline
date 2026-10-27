// The handoff prompt on the phone's settings, as the PC's card: the built-in text shown in the box, a way back to it, what the agent reads of it,
// and a warning when agents set their own. The guards are app-core's, so both apps refuse and join the same way.
import { useEffect, useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { ownTextWarning, promptPreview, promptProblem, promptToSave, type HandoffSettings } from '@handline/app-core';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';

export function HandoffPrompt({ settings }: { settings: HandoffSettings }) {
  const c = usePalette();
  const save = useWrite('settings.set');
  const why = useOff('settings.set');
  const builtin = settings.handoff_prompt_default;
  const current = settings.values.handoff_prompt ?? '';
  const [text, setText] = useState(current);
  const [showOwn, setShowOwn] = useState(false);
  useEffect(() => setText(current), [current]);
  useEffect(() => {
    if (save.isError) setText(current);
  }, [save.isError, current]);
  const bad = promptProblem(text);
  const isBuiltin = builtin !== undefined && current === builtin;
  const own = settings.handoff_prompt_own ?? [];
  const warning = ownTextWarning(own);
  const commit = () => {
    if (bad) return;
    const to = promptToSave(text, builtin);
    if (to !== (isBuiltin ? '' : current)) save.mutate({ changes: { handoff_prompt: to } });
  };
  const note = bad ?? (save.error ? `not saved: ${sentence(classify(save.error))}` : save.isPending ? 'saving…' : null);
  const small = { color: c.muted, fontSize: font.size.label } as const;
  return (
    <View testID="setting-prompt" style={{ gap: space.xs }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>What the agent is asked</Text>
      <Text style={small}>
        {"{path} is the note's file and {max} its size limit. Without {path}, the file is named on a line of its own."}
      </Text>
      <TextInput
        testID="prompt-input"
        accessibilityLabel="What the agent is asked"
        value={text}
        onChangeText={setText}
        onBlur={commit}
        multiline
        editable={!why}
        style={{
          color: c.ink,
          borderWidth: 1,
          borderColor: bad ? c.block : c.line,
          borderRadius: radius.card,
          padding: space.md,
          minHeight: 120,
          textAlignVertical: 'top',
        }}
      />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        {isBuiltin ? (
          <Text testID="prompt-builtin" style={{ ...small, fontWeight: '600' }}>
            Built-in
          </Text>
        ) : null}
        <Button
          testID="prompt-reset"
          label="Reset to built-in"
          disabled={!!why || (builtin === undefined ? !current : isBuiltin) || save.isPending}
          onPress={() => save.mutate({ changes: { handoff_prompt: '' } })}
        />
        <Button testID="prompt-save" label="Save" onPress={commit} disabled={!!why} />
      </View>
      {note ? (
        <Text
          testID="prompt-note"
          accessibilityRole={bad || save.error ? 'alert' : undefined}
          style={{ ...small, color: bad || save.error ? c.block : c.muted }}
        >
          {note}
        </Text>
      ) : null}
      <View style={{ backgroundColor: c.panel, borderRadius: radius.card, padding: space.md, gap: space.xs }}>
        <Text style={{ color: c.ink, fontWeight: '600', fontSize: font.size.label }}>The agent reads:</Text>
        <Text testID="prompt-preview" style={small}>
          {promptPreview(bad ? current : text || builtin || '')}
        </Text>
      </View>
      {warning ? (
        <View style={{ gap: space.xs }}>
          <Text testID="prompt-own" style={{ ...small, color: c.wait }}>
            {warning}
          </Text>
          <Button testID="prompt-own-show" label={showOwn ? 'Hide' : 'Show'} onPress={() => setShowOwn(!showOwn)} />
          {showOwn ? <Text style={small}>{own.join(', ')}</Text> : null}
        </View>
      ) : null}
    </View>
  );
}
