// The account page's settings and Remove (alias, cap, recovery, the cycle; Remove with its dry run and the name typed), and the input they share.
import { useEffect, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import type { Account } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useOff, usePending, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

export function Input(props: { value: string; onChange: (v: string) => void; label: string; testID: string; keyboardType?: 'number-pad' }) {
  const c = usePalette();
  return (
    <TextInput
      testID={props.testID}
      accessibilityLabel={props.label}
      placeholder={props.label}
      placeholderTextColor={c.muted}
      value={props.value}
      onChangeText={props.onChange}
      keyboardType={props.keyboardType}
      autoCapitalize="none"
      style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.m, padding: space.md, minHeight: 44 }}
    />
  );
}

export function Settings({ a }: { a: Account }) {
  const c = usePalette();
  const save = useWrite('account.set', a.name);
  const off = useOff('account.set');
  const was = {
    alias: a.label === a.name ? '' : a.label,
    cap: a.cap === null ? '' : String(a.cap),
    end: a.cycle.end_is_default ? '' : (a.cycle.end ?? ''),
  };
  const [alias, setAlias] = useState(was.alias);
  const [cap, setCap] = useState(was.cap);
  const [end, setEnd] = useState(was.end);
  // Another client's change arrives through the topic and replaces what the boxes show.
  const [seen, setSeen] = useState(was);
  if (seen.alias !== was.alias || seen.cap !== was.cap || seen.end !== was.end) {
    setSeen(was);
    if (seen.alias !== was.alias) setAlias(was.alias);
    if (seen.cap !== was.cap) setCap(was.cap);
    if (seen.end !== was.end) setEnd(was.end);
  }
  const n = Number(cap);
  const badCap = cap !== '' && !(Number.isInteger(n) && n >= 1);
  const badEnd = end !== '' && !/^\d{4}-\d{2}-\d{2}$/.test(end.trim());
  const changed = alias.trim() !== was.alias || (cap !== was.cap && cap !== '') || end.trim() !== was.end;
  const submit = () => {
    const p: { account: string; label?: string; cap?: number; cycle_end?: string | null } = { account: a.name };
    if (alias.trim() !== was.alias) p.label = alias.trim();
    if (cap !== was.cap && cap !== '') p.cap = n;
    if (end.trim() !== was.end) p.cycle_end = end.trim() || null;
    save.mutate(p);
  };
  return (
    <View testID="account-settings" style={{ gap: space.sm }}>
      <Input testID="account-alias" label="Alias" value={alias} onChange={setAlias} />
      <Input testID="account-cap" label={`Cap (now ${a.cap_effective} agents)`} value={cap} onChange={setCap} keyboardType="number-pad" />
      {badCap ? (
        <Text accessibilityRole="alert" style={{ color: c.block }}>
          The cap is a whole number, 1 or more.
        </Text>
      ) : null}
      <Toggle
        testID="account-recover"
        label="Usage recovery"
        on={a.recover}
        disabled={!!off}
        onChange={(on) => save.mutate({ account: a.name, recover: on })}
      />
      <Toggle
        testID="account-cycle"
        label="Ends this month"
        on={a.cycle.on}
        disabled={!!off}
        onChange={(on) => save.mutate({ account: a.name, cycle: on })}
      />
      <Input testID="account-cycle-end" label="Last day, as 2026-10-31 (blank: when its login drops)" value={end} onChange={setEnd} />
      {badEnd ? (
        <Text accessibilityRole="alert" style={{ color: c.block }}>
          The last day is a date like 2026-10-31.
        </Text>
      ) : null}
      <Button
        testID="account-save"
        label={save.isPending ? 'Saving…' : 'Save'}
        disabled={!!off || badCap || badEnd || !changed || save.isPending}
        onPress={submit}
      />
      {off ? <Text style={{ color: c.muted }}>Locked: {off}.</Text> : null}
    </View>
  );
}

function Toggle(props: { label: string; on: boolean; disabled: boolean; onChange: (on: boolean) => void; testID: string }) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole="switch"
      accessibilityLabel={props.label}
      accessibilityState={{ checked: props.on, disabled: props.disabled }}
      disabled={props.disabled}
      onPress={() => props.onChange(!props.on)}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        minHeight: 44,
        opacity: props.disabled ? 0.4 : 1,
      }}
    >
      <Text style={{ color: c.ink }}>{props.label}</Text>
      <Text style={{ color: props.on ? c.run : c.muted, fontWeight: '700' }}>{props.on ? 'On' : 'Off'}</Text>
    </Pressable>
  );
}

export function Remove({ a }: { a: Account }) {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const off = useOff('account.remove');
  const [asking, setAsking] = useState(false);
  if (!founder) return null;
  return (
    <View testID="account-remove" style={{ gap: space.sm }}>
      <Button testID="account-remove-open" kind="no" label="Remove account…" disabled={!!off || asking} onPress={() => setAsking(true)} />
      {off ? <Text style={{ color: c.muted }}>Locked: {off}.</Text> : null}
      {asking ? <RemoveSheet a={a} onClose={() => setAsking(false)} /> : null}
    </View>
  );
}

function RemoveSheet({ a, onClose }: { a: Account; onClose: () => void }) {
  const c = usePalette();
  const plan = useWrite('account.remove', a.name);
  const remove = useWrite('account.remove', a.name);
  const removing = usePending('account.remove').some((v) => v.account === a.name && !v.dry_run);
  const [typed, setTyped] = useState('');
  const { mutate: dry } = plan;
  useEffect(() => dry({ account: a.name, dry_run: true }), [dry, a.name]);
  const would = plan.data?.would_remove === true;
  const failed = plan.error ?? remove.error;
  return (
    <Sheet
      label={`Remove ${a.label}`}
      submit={removing ? 'Removing…' : 'Remove account'}
      kind="no"
      busy={!would || typed !== a.name || removing}
      onCancel={onClose}
      onSubmit={() => remove.mutate({ account: a.name, confirm: typed }, { onSuccess: onClose })}
      error={failed ? sentence(classify(failed)) : undefined}
    >
      {plan.isPending ? <Text style={{ color: c.muted }}>Checking what removing it would do…</Text> : null}
      {a.agents.length ? <Text style={{ color: c.wait }}>On it now: {a.agents.join(', ')}. Move them off first.</Text> : null}
      {(plan.data?.plan ?? []).map((l) => (
        <Text key={l} testID="plan-line" style={{ color: c.ink }}>
          • {l}
        </Text>
      ))}
      {would ? (
        <>
          <Input testID="sheet-confirm" label={`Type ${a.name} to confirm`} value={typed} onChange={setTyped} />
          <Text style={{ color: c.muted, fontSize: font.size.sm }}>Its directory is moved aside, not deleted.</Text>
        </>
      ) : null}
    </Sheet>
  );
}
