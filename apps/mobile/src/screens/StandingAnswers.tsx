// The standing answers of the phone's Settings: the list, Remove, and the Add sheet. Authored: a refusal comes back with the daemon's reason.
import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import type { CallParams } from '@handline/client';
import { classify, sentence, useAnswers, useOff, useServes, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Button } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

function Input(props: { value: string; onChange: (v: string) => void; label: string; testID: string }) {
  const c = usePalette();
  return (
    <TextInput
      testID={props.testID}
      accessibilityLabel={props.label}
      placeholder={props.label}
      placeholderTextColor={c.muted}
      value={props.value}
      onChangeText={props.onChange}
      autoCapitalize="none"
      autoCorrect={false}
      style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.m, padding: space.md, minHeight: 44 }}
    />
  );
}

function AddAnswer(props: { onAdd: (p: CallParams<'answer.add'>) => void; onDone: () => void }) {
  const c = usePalette();
  const why = useOff('answer.add');
  const [kind, setKind] = useState<'trust' | 'dialog'>('trust');
  const [f, setF] = useState({ folder: '', vendor: '', title: '', label: '' });
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const p = kind === 'trust' ? { folder: f.folder.trim() } : { vendor: f.vendor.trim(), title: f.title.trim(), label: f.label.trim() };
  const ready = Object.values(p).every(Boolean);
  return (
    <Sheet
      id="answer"
      label="Add a standing answer"
      submit="Add"
      busy={!ready || !!why}
      onCancel={props.onDone}
      onSubmit={() => {
        props.onAdd(p);
        props.onDone();
      }}
    >
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button testID="kind-trust" label="Trust a folder" kind={kind === 'trust' ? 'yes' : 'plain'} onPress={() => setKind('trust')} />
        <Button testID="kind-dialog" label="Answer a dialog" kind={kind === 'dialog' ? 'yes' : 'plain'} onPress={() => setKind('dialog')} />
      </View>
      {kind === 'trust' ? (
        <Input testID="answer-folder" label="Folder" value={f.folder} onChange={set('folder')} />
      ) : (
        <>
          <Input testID="answer-vendor" label="Vendor" value={f.vendor} onChange={set('vendor')} />
          <Input testID="answer-title" label="Prompt title" value={f.title} onChange={set('title')} />
          <Input testID="answer-option" label="Option" value={f.label} onChange={set('label')} />
        </>
      )}
      {why ? <Text style={{ color: c.muted, fontSize: font.size.sm }}>{why}</Text> : null}
    </Sheet>
  );
}

export function Answers() {
  const c = usePalette();
  const query = useAnswers();
  const { served } = useServes('answers.list');
  const add = useWrite('answer.add');
  const remove = useWrite('answer.remove');
  const whyAdd = useOff('answer.add');
  const whyRemove = useOff('answer.remove');
  const [adding, setAdding] = useState(false);
  const rows = query.data?.answers ?? [];
  const failed = add.error ?? remove.error;
  return (
    <View testID="answers" style={{ gap: space.sm }}>
      <Text style={{ color: c.ink, fontSize: font.size.lg, fontWeight: '700' }}>Standing answers</Text>
      <Loadable
        query={query}
        served={served}
        isEmpty={rows.length === 0}
        skeleton="settings"
        label="standing answers"
        empty="None. You are asked when a dialog shows."
      >
        <View>
          {rows.map((a) => (
            <View
              key={`${a.kind}\0${a.key.join('\0')}`}
              testID={`answer-${a.kind}`}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: space.md,
                paddingVertical: space.sm,
                borderTopWidth: 1,
                borderTopColor: c.line,
              }}
            >
              <Text style={{ flex: 1, color: c.ink }}>{a.text}</Text>
              <Button
                testID="answer-remove"
                label="Remove"
                disabled={!!whyRemove}
                onPress={() => {
                  add.reset();
                  remove.mutate({ kind: a.kind, key: a.key });
                }}
              />
            </View>
          ))}
        </View>
      </Loadable>
      {failed ? (
        <Text testID="answers-note" accessibilityRole="alert" style={{ color: c.block, fontSize: font.size.sm }}>
          not saved: {sentence(classify(failed))}
        </Text>
      ) : null}
      {served &&
        (adding ? (
          <AddAnswer
            onAdd={(p) => {
              remove.reset();
              add.mutate(p);
            }}
            onDone={() => setAdding(false)}
          />
        ) : (
          <Button testID="answer-new" label="Add" disabled={!!whyAdd} onPress={() => setAdding(true)} />
        ))}
    </View>
  );
}
