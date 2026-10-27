// The handoff prompt on the phone's settings, as the PC's card: the built-in text shown in the box, a way back to it, what the agent reads of it,
// and a warning when agents set their own. The guards are app-core's, so both apps refuse and join the same way.
import { useEffect, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { ownTextWarning, promptPreview, promptProblem, promptToSave, type HandoffSettings } from '@handline/app-core';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';
import { Icon } from '#src/screens/Icon.tsx';

export function HandoffPrompt({ settings }: { settings: HandoffSettings }) {
  const c = usePalette();
  const save = useWrite('settings.set');
  const why = useOff('settings.set');
  const builtin = settings.handoff_prompt_default;
  const current = settings.values.handoff_prompt ?? '';
  const [text, setText] = useState(current);
  const [showOwn, setShowOwn] = useState(false);
  const [showReads, setShowReads] = useState(false);
  const [focus, setFocus] = useState(false);
  useEffect(() => {
    if (!focus) setText(current);
  }, [current, focus]);
  useEffect(() => {
    if (save.isError) setText(current);
  }, [save.isError, current]);
  const bad = promptProblem(text);
  const isBuiltin = builtin !== undefined && current === builtin;
  const own = settings.handoff_prompt_own ?? [];
  const warning = ownTextWarning(own);
  const to = promptToSave(text, builtin);
  const dirty = to !== (isBuiltin ? '' : current);
  const commit = () => {
    if (!bad && dirty) save.mutate({ changes: { handoff_prompt: to } });
  };
  const reset = () => {
    setText(builtin ?? '');
    if (current && !isBuiltin) save.mutate({ changes: { handoff_prompt: '' } });
  };
  const note = save.error ? `not saved: ${sentence(classify(save.error))}` : save.isPending ? 'saving…' : null;
  const reads = promptPreview(bad ? current : text || builtin || '');
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
        onFocus={() => setFocus(true)}
        onBlur={() => {
          setFocus(false);
          commit();
        }}
        multiline
        editable={!why}
        style={{
          color: c.ink,
          borderWidth: 1,
          borderColor: bad ? c.block : c.line,
          borderRadius: radius.card,
          padding: space.md,
          minHeight: 96,
          textAlignVertical: 'top',
        }}
      />
      {bad ? (
        <View testID="prompt-refusal" accessibilityRole="alert" style={{ flexDirection: 'row', alignItems: 'flex-start', gap: space.xs }}>
          <Icon name="failed" color={c.block} size={14} label="Not saved" />
          <Text style={{ ...small, color: c.block, flexShrink: 1 }}>Not saved, the old text stays. {bad}</Text>
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        {isBuiltin && !dirty ? (
          <Text testID="prompt-builtin" style={{ ...small, fontWeight: '600' }}>
            Built-in
          </Text>
        ) : null}
        <Button
          testID="prompt-reset"
          label="Reset to built-in"
          disabled={!!why || (!dirty && (builtin === undefined ? !current : isBuiltin)) || save.isPending}
          onPress={reset}
        />
        <Button testID="prompt-save" label="Save" onPress={commit} disabled={!!why} />
      </View>
      {note ? (
        <Text
          testID="prompt-note"
          accessibilityRole={save.error ? 'alert' : undefined}
          style={{ ...small, color: save.error ? c.block : c.muted }}
        >
          {note}
        </Text>
      ) : null}
      <View>
        <Pressable
          testID="prompt-reads"
          accessibilityRole="button"
          accessibilityState={{ expanded: showReads }}
          accessibilityLabel={showReads ? 'Hide what the agent reads' : 'Show what the agent reads'}
          onPress={() => setShowReads(!showReads)}
          style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs, minHeight: 32 }}
        >
          <Icon name={showReads ? 'less' : 'more'} color={c.muted} size={14} decorative />
          <Text style={{ ...small, color: c.ink, fontWeight: '600' }}>What the agent reads</Text>
          {showReads ? null : (
            <Text style={{ ...small, flexShrink: 1 }} numberOfLines={1}>
              {reads.split('\n')[0]}
            </Text>
          )}
        </Pressable>
        {showReads ? (
          <View style={{ backgroundColor: c.panel, borderRadius: radius.card, padding: space.md }}>
            <Text testID="prompt-preview" style={small}>
              {reads}
            </Text>
          </View>
        ) : null}
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
