// An agent's settings on the phone (docs/pc-app-gaps.md G6, the bridge's agent_settings.py): every field saves on its own through agent.set,
// an authored write. A text or number field saves on Save or Enter, a choice or switch on a tap; a refusal puts the value back and the
// field's own note says why. The two danger switches ask first. Schedules sit below. Mount it with the agent's name and a way back.
import { useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { DANGER, FIELDS, mayChange, problemWith, type Field } from '@handline/app-core';
import { useSelector } from '@handline/app-react';
import { classify, sentence, useAgentDetail, usePending, useServes, useSettings, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';
import { Schedules } from './Schedules.tsx';

const SECTIONS: { id: Field['section']; title: string }[] = [
  { id: 'model', title: 'Model' },
  { id: 'work', title: 'Work' },
  { id: 'session', title: 'Session' },
  { id: 'browser', title: 'Browser' },
];

/** The unit after the field; what follows its comma ("min, 0 = never") goes in the hint. */
const unitOf = (f: Field) => f.unit?.split(',')[0]?.trim();
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
      <View style={{ padding: space.lg, gap: space.sm }}>
        <Button testID="settings-back" label={`‹ ${agent}`} onPress={onBack} style={{ alignSelf: 'flex-start' }} />
        <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700' }}>Settings</Text>
        <Text style={{ color: c.muted }}>Each change saves as you make it.</Text>
        {served ? null : <Text style={{ color: c.muted }}>This daemon does not serve agent settings.</Text>}
        {served && !editable ? <Text style={{ color: c.muted }}>Only the founder edits agent settings.</Text> : null}
      </View>
      <ScrollView testID="agent-settings" contentContainerStyle={{ padding: space.lg, paddingTop: 0, gap: space.xl }}>
        <Loadable query={query} served skeleton="settings" label={`${agent} settings`} empty="This agent no longer exists.">
          {d ? (
            <View style={{ gap: space.xl }}>
              <Text testID="session-account" style={{ color: c.ink }}>
                Session runs on <Text style={{ fontWeight: '700' }}>{d.account ?? 'not chosen yet'}</Text>
              </Text>
              {SECTIONS.map((s) => (
                <View key={s.id} testID={`section-${s.id}`} style={{ gap: space.lg }}>
                  <Text style={{ color: c.ink, fontSize: 16, fontWeight: '700' }}>{s.title}</Text>
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
  const { agent, f, role } = props;
  const stored = props.stored ?? '';
  const write = useWrite('agent.set', agent);
  const pending = usePending('agent.set').some((v) => v.agent === agent && f.key in v.values);
  const global = useSettings().data?.values?.handoff_note;
  const [draft, setDraft] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [ask, setAsk] = useState<string | null>(null); // a danger switch waiting for its confirmation
  const allowed = mayChange(f.key, role);
  const off = !props.editable || !allowed;
  const value = draft ?? stored;
  const send = (v: string) => {
    setDraft(null);
    setNote('');
    if (v === stored) return;
    write.mutateAsync({ agent, values: { [f.key]: v } }).catch((e) => setNote(sentence(classify(e))));
  };
  const commit = () => {
    const problem = problemWith(f, value);
    if (problem) return setNote(`${f.label}: ${problem}`);
    send(f.kind === 'number' ? String(Number(value)) : value.trim());
  };
  const choose = (v: string) => {
    if (f.kind === 'switch' && DANGER[f.key]?.to === v) return setAsk(v);
    send(v);
  };
  const options = f.kind === 'select' ? (f.options!.includes(value) ? f.options! : [value, ...f.options!]) : [];
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
                borderRadius: radius.md,
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
          onSubmitEditing={commit}
          style={{
            flex: 1,
            color: c.ink,
            borderWidth: 1,
            borderColor: note ? c.block : c.line,
            borderRadius: radius.md,
            padding: space.md,
            minHeight: 44,
            opacity: off ? 0.4 : 1,
          }}
        />
        {unitOf(f) ? <Text style={{ color: c.muted }}>{unitOf(f)}</Text> : null}
        <Button testID={`${f.key}-save`} label="Save" disabled={off || draft === null} onPress={commit} />
      </View>
    );
  };
  return (
    <View testID={`field-${f.key}`} style={{ gap: space.xs }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>
        {f.label}
        {pending ? '  saving…' : ''}
      </Text>
      {hint ? <Text style={{ color: c.muted, fontSize: 12 }}>{hint}</Text> : null}
      {control()}
      {note ? (
        <Text testID={`${f.key}-note`} accessibilityRole="alert" style={{ color: c.block, fontSize: 12 }}>
          not saved: {note}
        </Text>
      ) : null}
      {danger ? (
        <View
          testID={`${f.key}-ask`}
          accessibilityRole="alert"
          style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.block, borderRadius: radius.md }}
        >
          <Text style={{ color: c.ink, fontWeight: '700' }}>{danger.title}</Text>
          <Text style={{ color: c.muted }}>{danger.text}</Text>
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button
              testID={`${f.key}-go`}
              label={danger.go}
              kind="no"
              onPress={() => {
                const v = ask!;
                setAsk(null);
                send(v);
              }}
            />
            <Button testID={`${f.key}-cancel`} label="Cancel" onPress={() => setAsk(null)} />
          </View>
        </View>
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
              borderRadius: radius.md,
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
