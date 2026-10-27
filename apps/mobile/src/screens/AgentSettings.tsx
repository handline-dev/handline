// An agent's settings on the phone (docs/pc-app-gaps.md G6, the bridge's agent_settings.py): every field saves on its own through agent.set,
// an authored write. A text or number field saves on Save or Enter, a choice or switch on a tap; a refusal puts the value back and the
// field's own note says why. The two danger switches ask first. Schedules sit below. Mount it with the agent's name and a way back.
import { useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { DANGER, type Field, FIELDS, fieldUnit } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { useAgentDetail, useFieldRow, useServes } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Button, PageHeader } from '#src/screens/parts.tsx';
import { Schedules } from '#src/screens/Schedules.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

const SECTIONS: { id: Field['section']; title: string }[] = [
  { id: 'model', title: 'Model' },
  { id: 'work', title: 'Work' },
  { id: 'session', title: 'Session' },
  { id: 'browser', title: 'Browser' },
];

const hintOf = (f: Field) =>
  [f.note, f.unit?.includes(',') ? f.unit.split(',').slice(1).join(',').trim() : undefined].filter(Boolean).join('; ');

export function AgentSettings({ agent, onBack }: { agent: string; onBack: () => void }) {
  const c = usePalette();
  const query = useAgentDetail(agent);
  const { served } = useServes('agent.set');
  const role = useSelector((s) => s.client.hello?.role); // the page redraws when hello arrives
  const d = query.data;
  const editable = served && (role === 'founder' || role === 'agent'); // agent.set is for the founder and an agent on itself
  return (
    <View style={{ flex: 1 }}>
      <PageHeader title="Settings" back={{ label: agent, onPress: onBack, testID: 'settings-back' }}>
        <Text style={{ color: c.muted }}>
          {!served
            ? 'This needs a newer Handline.'
            : editable
              ? 'Each change saves as you make it.'
              : 'Only the founder edits agent settings.'}
        </Text>
      </PageHeader>
      <ScrollView testID="agent-settings" contentContainerStyle={{ padding: space.lg, paddingTop: 0, gap: space.xl }}>
        <Loadable query={query} served skeleton="settings" label={`${agent} settings`} empty="This agent no longer exists.">
          {d ? (
            <View style={{ gap: space.xl }}>
              <Text testID="session-account" style={{ color: c.ink }}>
                Session runs on <Text style={{ fontWeight: '700' }}>{d.account ?? 'not chosen yet'}</Text>
              </Text>
              {SECTIONS.map((s) => (
                <View key={s.id} testID={`section-${s.id}`} style={{ gap: space.lg }}>
                  <Text style={{ color: c.ink, fontSize: font.size.title, fontWeight: '700' }}>{s.title}</Text>
                  {FIELDS.filter((f) => f.section === s.id).map((f) => (
                    <FieldRow key={f.key} agent={agent} f={f} stored={d.settings?.[f.key]} editable={editable} role={role} />
                  ))}
                </View>
              ))}
              <Schedules agent={agent} role={role} />
            </View>
          ) : null}
        </Loadable>
      </ScrollView>
    </View>
  );
}

function FieldRow(props: { agent: string; f: Field; stored: string | undefined; editable: boolean; role: string | undefined }) {
  const c = usePalette();
  const { f } = props;
  const { stored, pending, global, draft, setDraft, note, setNote, ask, setAsk, allowed, off, value, send, commit, choose, options } =
    useFieldRow(props);
  const danger = ask ? DANGER[f.key] : undefined;
  const follows =
    f.key === 'handoff_note' ? `default follows the global switch${global === undefined ? '' : global ? ' (on)' : ' (off)'}` : '';
  const hint = [hintOf(f), follows, allowed ? '' : 'founder only'].filter(Boolean).join('; ');
  const control = () => {
    if (f.kind === 'select')
      return (
        <View accessibilityRole="radiogroup" accessibilityLabel={f.label} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xs }}>
          {options.map((o) => (
            <Pressable
              key={o}
              testID={`${f.key}-${o}`}
              accessibilityRole="radio"
              accessibilityState={{ selected: value === o, disabled: off }}
              disabled={off}
              onPress={() => choose(o)}
              style={{
                paddingVertical: space.sm,
                paddingHorizontal: space.md,
                minHeight: 36,
                justifyContent: 'center',
                borderWidth: 1,
                borderColor: value === o ? c.ink : c.line,
                backgroundColor: value === o ? c.panel : 'transparent',
                borderRadius: radius.card,
                opacity: off ? 0.4 : 1,
              }}
            >
              <Text style={{ color: value === o ? c.ink : c.muted, fontWeight: value === o ? '700' : '400' }}>{o}</Text>
            </Pressable>
          ))}
        </View>
      );
    if (f.kind === 'switch')
      return (
        <Pressable
          testID={`${f.key}-switch`}
          accessibilityRole="switch"
          accessibilityLabel={f.label}
          accessibilityState={{ checked: stored === 'on', disabled: off }}
          disabled={off}
          onPress={() => choose(stored === 'on' ? 'off' : 'on')}
          style={{ minHeight: 44, justifyContent: 'center', opacity: off ? 0.4 : 1 }}
        >
          <Text style={{ color: stored === 'on' ? c.run : c.muted, fontWeight: '700' }}>{stored === 'on' ? 'On' : 'Off'}</Text>
        </Pressable>
      );
    if (f.kind === 'sites') return <Sites value={stored} off={off} save={send} />;
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <TextInput
          testID={`${f.key}-input`}
          accessibilityLabel={f.label}
          value={value}
          editable={!off}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType={f.kind === 'number' ? 'number-pad' : 'default'}
          onChangeText={(t) => {
            setDraft(t);
            setNote('');
          }}
          onSubmitEditing={() => commit(value)}
          style={{
            flex: 1,
            color: c.ink,
            borderWidth: 1,
            borderColor: note ? c.block : c.line,
            borderRadius: radius.card,
            padding: space.md,
            minHeight: 44,
            opacity: off ? 0.4 : 1,
          }}
        />
        {fieldUnit(f) ? <Text style={{ color: c.muted }}>{fieldUnit(f)}</Text> : null}
        <Button testID={`${f.key}-save`} label="Save" disabled={off || draft === null} onPress={() => commit(value)} />
      </View>
    );
  };
  return (
    <View testID={`field-${f.key}`} style={{ gap: space.xs }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>
        {f.label}
        {pending ? '  saving…' : ''}
      </Text>
      {hint ? <Text style={{ color: c.muted, fontSize: font.size.label }}>{hint}</Text> : null}
      {control()}
      {note ? (
        <Text testID={`${f.key}-note`} accessibilityRole="alert" style={{ color: c.block, fontSize: font.size.label }}>
          not saved: {note}
        </Text>
      ) : null}
      {danger ? (
        <Sheet
          id={f.key}
          label={danger.title}
          submit={danger.go}
          kind="no"
          onCancel={() => setAsk(null)}
          onSubmit={() => {
            const v = ask!;
            setAsk(null);
            send(v);
          }}
        >
          <Text style={{ color: c.muted }}>{danger.text}</Text>
        </Sheet>
      ) : null}
    </View>
  );
}

/** The allowed sites: a space-separated string in the contract, a list with add and remove here. */
function Sites({ value, off, save }: { value: string; off: boolean; save: (v: string) => void }) {
  const c = usePalette();
  const sites = value.split(/\s+/).filter(Boolean);
  const [text, setText] = useState('');
  const add = () => {
    const site = text.trim();
    setText('');
    if (site && !sites.includes(site)) save([...sites, site].join(' '));
  };
  return (
    <View testID="sites" style={{ gap: space.sm }}>
      {sites.map((s) => (
        <View key={s} testID={`site-${s}`} style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <Text style={{ color: c.ink, flex: 1 }}>{s}</Text>
          <Button testID="site-remove" label="Remove" disabled={off} onPress={() => save(sites.filter((x) => x !== s).join(' '))} />
        </View>
      ))}
      {off ? null : (
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <TextInput
            testID="site-input"
            accessibilityLabel="Add a site"
            placeholder="https://…"
            placeholderTextColor={c.muted}
            autoCapitalize="none"
            autoCorrect={false}
            value={text}
            onChangeText={setText}
            onSubmitEditing={add}
            style={{
              flex: 1,
              color: c.ink,
              borderWidth: 1,
              borderColor: c.line,
              borderRadius: radius.card,
              padding: space.md,
              minHeight: 44,
            }}
          />
          <Button testID="site-add" label="Add" onPress={add} />
        </View>
      )}
    </View>
  );
}
