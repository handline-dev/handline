// The phone's Home settings (docs/pc-app-gaps.md G11): default cap and idle close as fields that save on Save, three switches, and the standing
// answers with Add and Remove. All of it is authored: a change shows at once and goes back with the daemon's reason when refused.
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { INVALID, capField, idleField, parseSeconds, type PageSettings } from '@handline/app-core';
import { classify, sentence, useOff, useServes, useSettings, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { useSelector } from '@handline/app-react';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Answers } from '#src/screens/StandingAnswers.tsx';
import { AppUpdate } from '#src/screens/AppUpdate.tsx';
import { Button, PageHeader } from '#src/screens/parts.tsx';
import { HandoffPrompt } from '#src/screens/HandoffPrompt.tsx';
import { Connection } from '#src/screens/Connection.tsx';
import { PairedDevices } from '#src/screens/PairedDevices.tsx';

function Note(props: { error: unknown; pending: boolean; bad?: string | null; testID: string }) {
  const c = usePalette();
  const text = props.bad ?? (props.error ? `not saved: ${sentence(classify(props.error))}` : null);
  if (!props.pending && !text) return null;
  return (
    <Text
      testID={props.testID}
      accessibilityRole={text ? 'alert' : undefined}
      style={{ color: text ? c.block : c.muted, fontSize: font.size.label }}
    >
      {text ?? 'saving…'}
    </Text>
  );
}

function Field<T extends number | string>(props: {
  id: string;
  label: string;
  hint: string;
  unit?: string;
  current: string;
  parse: (t: string) => T | null | typeof INVALID;
  change: (v: T | null) => PageSettings;
  allowed: string;
  text?: boolean;
}) {
  const c = usePalette();
  const save = useWrite('settings.set');
  const why = useOff('settings.set');
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
      <Text style={{ color: c.muted, fontSize: font.size.label }}>{props.hint}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <TextInput
          testID={`${props.id}-input`}
          accessibilityLabel={props.label}
          value={text}
          onChangeText={setText}
          onSubmitEditing={commit}
          onBlur={commit}
          keyboardType={props.text ? 'default' : 'number-pad'}
          editable={!why}
          style={{
            flex: 1,
            color: c.ink,
            borderWidth: 1,
            borderColor: bad ? c.block : c.line,
            borderRadius: radius.card,
            padding: space.md,
            minHeight: 44,
          }}
        />
        {props.unit ? <Text style={{ color: c.muted }}>{props.unit}</Text> : null}
        <Button testID={`${props.id}-save`} label="Save" onPress={commit} disabled={!!why} />
      </View>
      {why ? <Text style={{ color: c.muted, fontSize: font.size.label }}>{why}</Text> : null}
      <Note testID={`${props.id}-note`} error={save.error} pending={save.isPending} bad={bad} />
    </View>
  );
}

const SECONDS = 'whole seconds, or empty for the default';
const secondsOf = (v: number | undefined) => (v === undefined ? '' : String(v));
const LIMIT = 'a whole number, 0 for off, or empty for the default';

/** A seconds row (as the web's NumberRow of Background and Notifications). */
function Seconds(props: {
  id: string;
  label: string;
  hint: string;
  current: number | undefined;
  name: 'bg_notice_s' | 'bg_grace_s' | 'bg_check_s' | 'notice_digest_s';
}) {
  return (
    <Field
      id={props.id}
      label={props.label}
      hint={props.hint}
      unit="s"
      current={secondsOf(props.current)}
      parse={parseSeconds}
      change={(n) => ({ [props.name]: n })}
      allowed={SECONDS}
    />
  );
}

/** A machine load limit: a whole number, 0 for off. */
function Limit(props: {
  id: string;
  label: string;
  hint: string;
  unit: string;
  current: number | undefined;
  name: 'host_active_cap' | 'load_per_core_pct' | 'ram_free_min_pct';
}) {
  return (
    <Field
      id={props.id}
      label={props.label}
      hint={props.hint}
      unit={props.unit}
      current={secondsOf(props.current)}
      parse={parseSeconds}
      change={(n) => ({ [props.name]: n })}
      allowed={LIMIT}
    />
  );
}

/** A whole number from `min` to `max`, or empty for the default. */
const inRange = (min: number, max: number) => (t: string) => {
  const n = parseSeconds(t);
  return n === null || (n !== INVALID && n >= min && n <= max) ? n : INVALID;
};
const parseTarget = inRange(50, 100);
const parseBurst = inRange(0, 50);

function Toggle(props: { id: string; label: string; hint: string; on: boolean; change: (on: boolean) => PageSettings }) {
  const c = usePalette();
  const save = useWrite('settings.set');
  const why = useOff('settings.set');
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
          <Text style={{ color: c.muted, fontSize: font.size.label }}>{props.hint}</Text>
        </View>
        <Text style={{ color: props.on ? c.run : c.muted, fontWeight: '700' }}>{props.on ? 'On' : 'Off'}</Text>
      </Pressable>
      <Note testID={`${props.id}-note`} error={save.error} pending={save.isPending} />
    </View>
  );
}

/** Where this phone and, later, other devices (a cluster, remote access) are paired. */
function Devices(props: { onUnpair: () => void }) {
  const c = usePalette();
  return (
    <View testID="devices" style={{ gap: space.sm }}>
      <Connection />
      <Text style={{ color: c.ink, fontWeight: '700' }}>Devices</Text>
      <Text style={{ color: c.muted }}>This phone</Text>
      <Button testID="unpair" label="Unpair this phone" onPress={props.onUnpair} style={{ alignSelf: 'flex-start' }} />
      <PairedDevices />
    </View>
  );
}

/** The Agents header's menu opens this: the founder's defaults, then the devices. */
export function SettingsPage(props: { onClose: () => void; onUnpair: () => void }) {
  const c = usePalette();
  const founder = useSelector((st) => st.client.hello?.role) === 'founder'; // the settings and standing answers are the founder's alone
  return (
    <View style={{ flex: 1 }}>
      <PageHeader title="Settings" back={{ label: 'Agents', onPress: props.onClose, testID: 'settings-close' }} />
      {founder ? (
        <Settings footer={<Devices onUnpair={props.onUnpair} />} />
      ) : (
        <ScrollView contentContainerStyle={{ padding: space.lg, paddingTop: 0, gap: space.xl }}>
          <Text testID="settings-founder-only" style={{ color: c.muted }}>
            Only the founder changes settings.
          </Text>
          <Devices onUnpair={props.onUnpair} />
        </ScrollView>
      )}
    </View>
  );
}

function Settings(props: { footer: React.ReactNode }) {
  const c = usePalette();
  const query = useSettings();
  const { served } = useServes('settings.get');
  const v = query.data?.values;
  return (
    <ScrollView testID="settings-page" contentContainerStyle={{ padding: space.lg, paddingTop: 0, gap: space.xl }}>
      <AppUpdate />
      <Text style={{ color: c.muted }}>Defaults for every agent. Each change saves as you make it.</Text>
      <Loadable query={query} served={served} skeleton="settings" label="settings" empty="No settings to show.">
        {v ? (
          <View style={{ gap: space.lg }}>
            <Field
              id="cap"
              {...capField(v.active_cap)}
              hint="Agents working at once on an account. An account with its own cap keeps it."
            />
            <Field id="idle" {...idleField(v.park_timeout_s)} hint="Idle time before a session closes. 0 is never." />
            <Limit
              id="host-cap"
              label="Agents on this machine"
              hint="Most agents working at once over every account. 0 is no limit."
              unit="agents"
              current={v.host_active_cap}
              name="host_active_cap"
            />
            <Limit
              id="load"
              label="Load per core"
              hint="Most 1-minute load per core; 150 is 1.5. 0 is off."
              unit="%"
              current={v.load_per_core_pct}
              name="load_per_core_pct"
            />
            <Limit
              id="ram"
              label="Least free memory"
              hint="No new work is started under this share of memory. 0 is off."
              unit="%"
              current={v.ram_free_min_pct}
              name="ram_free_min_pct"
            />
            {v.pacing === undefined ? null : (
              <>
                <Toggle
                  id="pacing"
                  label="Pacing"
                  hint="Hold each account to its weekly pace line: one ahead of it starts fewer agents"
                  on={v.pacing}
                  change={(on) => ({ pacing: on })}
                />
                <Field
                  id="pace-target"
                  label="Pace target"
                  hint="Weekly usage an account may reach by the end of its week. Empty is the default."
                  unit="%"
                  current={secondsOf(v.pace_target_pct)}
                  parse={parseTarget}
                  change={(n) => ({ pace_target_pct: n })}
                  allowed="a whole number from 50 to 100, or empty for the default"
                />
                <Field
                  id="pace-burst"
                  label="Pace burst"
                  hint="Points of the week an account may run ahead of its line before it starts nothing. Empty is the default."
                  unit="points"
                  current={secondsOf(v.pace_burst_pct)}
                  parse={parseBurst}
                  change={(n) => ({ pace_burst_pct: n })}
                  allowed="a whole number from 0 to 50, or empty for the default"
                />
              </>
            )}
            <Seconds
              id="bg-notice"
              label="Background notice"
              hint="Idle time before an agent with background work is told to finish or stop it. Empty follows Idle close."
              current={v.bg_notice_s}
              name="bg_notice_s"
            />
            <Seconds
              id="bg-grace"
              label="Background grace"
              hint="Time after that notice before the work is stopped"
              current={v.bg_grace_s}
              name="bg_grace_s"
            />
            <Seconds
              id="bg-check"
              label="Background check"
              hint="How often an agent with background work is asked which to stop"
              current={v.bg_check_s}
              name="bg_check_s"
            />
            <Seconds
              id="digest"
              label="Notice batch"
              hint="Wait this long after the first notice, then tell the agent all of them at once. Never in the middle of a turn; a message from a person comes sooner."
              current={v.notice_digest_s}
              name="notice_digest_s"
            />
            <Toggle
              id="handoff"
              label="Handoff note"
              hint="Before a session closes, the agent writes a note for the next one"
              on={!!v.handoff_note}
              change={(on) => ({ handoff_note: on })}
            />
            <HandoffPrompt settings={query.data!} />
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
      {props.footer}
    </ScrollView>
  );
}
