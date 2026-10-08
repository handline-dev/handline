// The phone's Home settings (docs/pc-app-gaps.md G11): default cap and idle close as fields that save on Save, three switches, and the standing
// answers with Add and Remove. All of it is authored: a change shows at once and goes back with the daemon's reason when refused.
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { INVALID, minutesOf, parseCap, parseMinutes } from '@handline/app-core';
import type { CallParams, SettingsChanges } from '@handline/client';
import { classify, sentence, useAnswers, useServes, useSettings, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';
import { useWhyNot } from './TaskActions.tsx';

function Note(props: { error: unknown; pending: boolean; bad?: string | null; testID: string }) {
  const c = usePalette();
  const text = props.bad ?? (props.error ? `not saved: ${sentence(classify(props.error))}` : null);
  if (!props.pending && !text) return null;
  return (
    <Text testID={props.testID} accessibilityRole={text ? 'alert' : undefined} style={{ color: text ? c.block : c.muted, fontSize: 12 }}>
      {text ?? 'saving…'}
    </Text>
  );
}

function Field(props: {
  id: string;
  label: string;
  hint: string;
  unit: string;
  current: string;
  parse: (t: string) => number | null | typeof INVALID;
  change: (v: number | null) => SettingsChanges;
  allowed: string;
}) {
  const c = usePalette();
  const save = useWrite('settings.set');
  const why = useWhyNot('settings.set');
  const [text, setText] = useState(props.current);
  const [bad, setBad] = useState<string | null>(null);
  const { current } = props;
  useEffect(() => setText(current), [current]);
  // A refusal puts the field back to what the daemon has.
  useEffect(() => {
    if (save.isError) setText(current);
  }, [save.isError, current]);
  const commit = () => {
    const v = props.parse(text);
    if (v === INVALID) return setBad(props.allowed);
    setBad(null);
    if (text.trim() !== current.trim()) save.mutate({ changes: props.change(v) });
  };
  return (
    <View testID={`setting-${props.id}`} style={{ gap: space.xs }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>{props.label}</Text>
      <Text style={{ color: c.muted, fontSize: 12 }}>{props.hint}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <TextInput
          testID={`${props.id}-input`}
          accessibilityLabel={props.label}
          value={text}
          onChangeText={setText}
          onSubmitEditing={commit}
          onBlur={commit}
          keyboardType="number-pad"
          editable={!why}
          style={{
            flex: 1,
            color: c.ink,
            borderWidth: 1,
            borderColor: bad ? c.block : c.line,
            borderRadius: radius.md,
            padding: space.md,
            minHeight: 44,
          }}
        />
        <Text style={{ color: c.muted }}>{props.unit}</Text>
        <Button testID={`${props.id}-save`} label="Save" onPress={commit} disabled={!!why} />
      </View>
      {why ? <Text style={{ color: c.muted, fontSize: 12 }}>{why}</Text> : null}
      <Note testID={`${props.id}-note`} error={save.error} pending={save.isPending} bad={bad} />
    </View>
  );
}

function Toggle(props: { id: string; label: string; hint: string; on: boolean; change: (on: boolean) => SettingsChanges }) {
  const c = usePalette();
  const save = useWrite('settings.set');
  const why = useWhyNot('settings.set');
  return (
    <View testID={`setting-${props.id}`} style={{ gap: space.xs }}>
      <Pressable
        testID={`${props.id}-switch`}
        accessibilityRole="switch"
        accessibilityLabel={props.label}
        accessibilityState={{ checked: props.on, disabled: !!why }}
        disabled={!!why}
        onPress={() => save.mutate({ changes: props.change(!props.on) })}
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: space.md,
          minHeight: 44,
          opacity: why ? 0.4 : 1,
        }}
      >
        <View style={{ flex: 1 }}>
          <Text style={{ color: c.ink, fontWeight: '600' }}>{props.label}</Text>
          <Text style={{ color: c.muted, fontSize: 12 }}>{props.hint}</Text>
        </View>
        <Text style={{ color: props.on ? c.run : c.muted, fontWeight: '700' }}>{props.on ? 'On' : 'Off'}</Text>
      </Pressable>
      <Note testID={`${props.id}-note`} error={save.error} pending={save.isPending} />
    </View>
  );
}

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
      style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 44 }}
    />
  );
}

function AddAnswer(props: { onAdd: (p: CallParams<'answer.add'>) => void; onDone: () => void }) {
  const c = usePalette();
  const why = useWhyNot('answer.add');
  const [kind, setKind] = useState<'trust' | 'dialog'>('trust');
  const [f, setF] = useState({ folder: '', vendor: '', title: '', label: '' });
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const p = kind === 'trust' ? { folder: f.folder.trim() } : { vendor: f.vendor.trim(), title: f.title.trim(), label: f.label.trim() };
  const ready = Object.values(p).every(Boolean);
  return (
    <View testID="answer-form" style={{ gap: space.sm }}>
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
      {why ? <Text style={{ color: c.muted, fontSize: 12 }}>{why}</Text> : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button
          testID="answer-add"
          label="Add"
          kind="yes"
          disabled={!ready || !!why}
          onPress={() => {
            props.onAdd(p);
            props.onDone();
          }}
        />
        <Button testID="answer-cancel" label="Cancel" onPress={props.onDone} />
      </View>
    </View>
  );
}

function Answers() {
  const c = usePalette();
  const query = useAnswers();
  const { served } = useServes('answers.list');
  const add = useWrite('answer.add');
  const remove = useWrite('answer.remove');
  const whyAdd = useWhyNot('answer.add');
  const whyRemove = useWhyNot('answer.remove');
  const [adding, setAdding] = useState(false);
  const rows = query.data?.answers ?? [];
  const failed = add.error ?? remove.error;
  return (
    <View testID="answers" style={{ gap: space.sm }}>
      <Text style={{ color: c.ink, fontSize: 16, fontWeight: '700' }}>Standing answers</Text>
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
        <Text testID="answers-note" accessibilityRole="alert" style={{ color: c.block, fontSize: 12 }}>
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

export function Settings() {
  const c = usePalette();
  const query = useSettings();
  const { served } = useServes('settings.get');
  const v = query.data?.values;
  return (
    <ScrollView testID="settings-page" contentContainerStyle={{ padding: space.lg, paddingTop: 0, gap: space.xl }}>
      <Text style={{ color: c.muted }}>Defaults for every agent. Each change saves as you make it.</Text>
      <Loadable query={query} served={served} skeleton="settings" label="settings" empty="No settings to show.">
        {v ? (
          <View style={{ gap: space.lg }}>
            <Field
              id="cap"
              label="Default cap"
              hint="Agents working at once on an account. An account with its own cap keeps it."
              unit="agents"
              current={v.active_cap === undefined ? '' : String(v.active_cap)}
              parse={parseCap}
              change={(n) => ({ active_cap: n })}
              allowed="a whole number from 1, or empty for the default"
            />
            <Field
              id="idle"
              label="Idle close"
              hint="Idle time before a session closes. 0 is never."
              unit="min"
              current={minutesOf(v.park_timeout_s)}
              parse={parseMinutes}
              change={(s) => ({ park_timeout_s: s })}
              allowed="whole minutes, 0 for never, or empty for the default"
            />
            <Toggle
              id="handoff"
              label="Handoff note"
              hint="A note for the next session before a close or move"
              on={!!v.handoff_note}
              change={(on) => ({ handoff_note: on })}
            />
            <Toggle
              id="auto"
              label="Auto-accept"
              hint="Answer the dialogs under Standing answers by itself"
              on={!!v.auto_accept_dialogs}
              change={(on) => ({ auto_accept_dialogs: on })}
            />
            <Toggle
              id="cache"
              label="Ignore cache expiry"
              hint="Resume the old session even after its cache went cold"
              on={!!v.ignore_cache_expiry}
              change={(on) => ({ ignore_cache_expiry: on })}
            />
          </View>
        ) : null}
      </Loadable>
      <Answers />
    </ScrollView>
  );
}
