// The Accounts tab (docs/pc-app-gaps.md G9): one line per account with its gauges, state and agents; one account with its alias and cap to
// edit (authored: shown at once, put back with the daemon's reason on a refusal) and, for the founder, Remove with a dry run and the name
// typed to confirm (decided: nothing changes until the daemon accepts).
import { useEffect, useState } from 'react';
import { FlatList, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { FIVE_HOURS_MS, STATE_WORD, WEEK_MS, gaugeOf, outUntil, span, stateOf } from '@handline/app-core';
import type { Account, Window } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useAccounts, usePending, useServes, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { AccountLogin } from './AccountLogin.tsx';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';
import { useWhyNot } from './TaskActions.tsx';

const SPANS = { '5 h': FIVE_HOURS_MS, week: WEEK_MS } as const;

export function Accounts(props: { selected: string | null; onSelect: (name: string | null) => void }) {
  return props.selected != null ? <One name={props.selected} onBack={() => props.onSelect(null)} /> : <List onSelect={props.onSelect} />;
}

function Gauge(props: { label: keyof typeof SPANS; w: Window | null; detail?: boolean }) {
  const c = usePalette();
  if (!props.w) return <Text style={{ color: c.muted }}>{props.label} no reading</Text>;
  const g = gaugeOf(props.w, SPANS[props.label], Date.now());
  const tone = g.tone === 'bad' ? c.block : g.tone === 'warn' ? c.wait : c.run;
  return (
    <View testID={`gauge-${props.label}`} style={{ gap: 2 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <Text style={{ color: c.muted, width: 44 }}>{props.label}</Text>
        <View style={{ flex: 1, height: 6, borderRadius: 3, backgroundColor: c.line }}>
          <View style={{ width: `${g.pct}%`, height: 6, borderRadius: 3, backgroundColor: tone }} />
        </View>
        <Text style={{ color: c.ink, width: 48, textAlign: 'right' }}>
          {g.pct}%{g.pct >= 100 ? ' full' : g.pct >= 80 ? ' !' : ''}
        </Text>
      </View>
      {props.detail && g.resetIn !== null ? <Text style={{ color: c.muted, fontSize: 12 }}>resets in {span(g.resetIn)}</Text> : null}
    </View>
  );
}

/** The state as a word, never only a colour. */
function stateText(a: Account): string {
  const s = stateOf(a);
  const until = s === 'out' ? outUntil(a) : null;
  return until ? `out until ${span(Date.parse(until) - Date.now())} from now` : STATE_WORD[s];
}

function List(props: { onSelect: (name: string) => void }) {
  const c = usePalette();
  const query = useAccounts();
  const { served } = useServes('accounts.list');
  const rows = query.data?.accounts ?? [];
  return (
    <View style={{ flex: 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: space.lg }}>
        <Text style={{ color: c.ink, fontSize: 22, fontWeight: '700' }}>Accounts</Text>
        <Add />
      </View>
      <Loadable
        query={query}
        served={served}
        isEmpty={rows.length === 0}
        skeleton="accounts"
        label="accounts"
        empty="No accounts configured."
      >
        <FlatList
          data={rows}
          keyExtractor={(a) => a.name}
          renderItem={({ item: a }) => (
            <Pressable
              testID={`account-${a.name}`}
              onPress={() => props.onSelect(a.name)}
              style={{ padding: space.lg, gap: space.xs, borderTopWidth: 1, borderTopColor: c.line }}
            >
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Text style={{ color: c.ink, fontWeight: '600' }}>{a.label}</Text>
                <Text style={{ color: c.muted }}>
                  {a.agents.length}/{a.cap_effective} agents
                </Text>
              </View>
              <Text style={{ color: c.muted, fontSize: 12 }}>{stateText(a)}</Text>
              <Gauge label="5 h" w={a.five_hour} />
              <Gauge label="week" w={a.weekly} />
            </Pressable>
          )}
        />
      </Loadable>
    </View>
  );
}

/** Founder only: a name registers the account, which then needs a login (the topic brings the row in). */
function Add() {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const off = useWhyNot('account.add');
  const [asking, setAsking] = useState(false);
  const [name, setName] = useState('');
  const add = useWrite('account.add', name);
  const adding = usePending('account.add').length > 0;
  if (!founder) return null;
  const close = () => (setAsking(false), setName(''), add.reset());
  return (
    <View style={{ gap: space.sm, alignItems: 'flex-end' }}>
      <Button testID="account-add-open" label="Add account" disabled={!!off || asking} onPress={() => setAsking(true)} />
      {off ? <Text style={{ color: c.muted }}>Locked: {off}.</Text> : null}
      {asking ? (
        <View
          testID="sheet"
          style={{
            gap: space.sm,
            padding: space.md,
            borderWidth: 1,
            borderColor: c.line,
            borderRadius: radius.md,
            backgroundColor: c.panel,
            alignSelf: 'stretch',
          }}
        >
          <Text style={{ color: c.ink, fontWeight: '600' }}>Add an account</Text>
          <Input testID="sheet-name" label="Name" value={name} onChange={setName} />
          <Text style={{ color: c.muted, fontSize: 12 }}>Log it in from its page once it is added.</Text>
          {add.error ? (
            <Text testID="sheet-error" accessibilityRole="alert" style={{ color: c.block }}>
              {sentence(classify(add.error))}
            </Text>
          ) : null}
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button
              testID="sheet-submit"
              label={adding ? 'Adding…' : 'Add account'}
              disabled={!name.trim() || adding}
              onPress={() => add.mutate({ account: name.trim() }, { onSuccess: close })}
              style={{ flex: 1 }}
            />
            <Button testID="sheet-cancel" label="Cancel" onPress={close} />
          </View>
        </View>
      ) : null}
    </View>
  );
}

function Input(props: { value: string; onChange: (v: string) => void; label: string; testID: string; keyboardType?: 'number-pad' }) {
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
      style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 44 }}
    />
  );
}

function One({ name, onBack }: { name: string; onBack: () => void }) {
  const c = usePalette();
  const query = useAccounts();
  const { served } = useServes('accounts.list');
  const a = query.data?.accounts.find((x) => x.name === name);
  return (
    <View style={{ flex: 1 }}>
      <Button testID="account-back" label="‹ Accounts" onPress={onBack} style={{ margin: space.lg, alignSelf: 'flex-start' }} />
      <Loadable query={query} served={served} skeleton="accounts" label="accounts" empty="This account no longer exists.">
        {a ? (
          <Body a={a} />
        ) : query.data ? (
          <Text testID="empty" style={{ color: c.muted, padding: space.lg }}>
            This account no longer exists.
          </Text>
        ) : null}
      </Loadable>
    </View>
  );
}

function Body({ a }: { a: Account }) {
  const c = usePalette();
  return (
    <ScrollView testID="account-detail" keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.lg }}>
      <Text style={{ color: c.ink, fontSize: 18, fontWeight: '700' }}>{a.label}</Text>
      <Text style={{ color: c.muted }}>{stateText(a)}</Text>
      <Gauge label="5 h" w={a.five_hour} detail />
      <Gauge label="week" w={a.weekly} detail />
      <Text style={{ color: c.ink }}>{a.agents.length ? `On it: ${a.agents.join(', ')}` : 'No agents are on it.'}</Text>
      <Settings a={a} />
      <AccountLogin a={a} />
      <Remove a={a} />
    </ScrollView>
  );
}

function Settings({ a }: { a: Account }) {
  const c = usePalette();
  const save = useWrite('account.set', a.name);
  const off = useWhyNot('account.set');
  const was = { alias: a.label === a.name ? '' : a.label, cap: a.cap === null ? '' : String(a.cap) };
  const [alias, setAlias] = useState(was.alias);
  const [cap, setCap] = useState(was.cap);
  // Another client's change arrives through the topic and replaces what the boxes show.
  useEffect(() => setAlias(was.alias), [was.alias]);
  useEffect(() => setCap(was.cap), [was.cap]);
  const n = Number(cap);
  const badCap = cap !== '' && !(Number.isInteger(n) && n >= 1);
  const changed = alias.trim() !== was.alias || (cap !== was.cap && cap !== '');
  const submit = () => {
    const p: { account: string; label?: string; cap?: number } = { account: a.name };
    if (alias.trim() !== was.alias) p.label = alias.trim();
    if (cap !== was.cap && cap !== '') p.cap = n;
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
      <Button
        testID="account-save"
        label={save.isPending ? 'Saving…' : 'Save'}
        disabled={!!off || badCap || !changed || save.isPending}
        onPress={submit}
      />
      {off ? <Text style={{ color: c.muted }}>Locked: {off}.</Text> : null}
    </View>
  );
}

function Remove({ a }: { a: Account }) {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const off = useWhyNot('account.remove');
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
    <View
      testID="sheet"
      style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, backgroundColor: c.panel }}
    >
      <Text style={{ color: c.ink, fontWeight: '600' }}>Remove {a.label}</Text>
      {plan.isPending ? <Text style={{ color: c.muted }}>Checking what removing it would do…</Text> : null}
      {a.agents.length ? <Text style={{ color: c.wait }}>On it now: {a.agents.join(', ')}. Move them off first.</Text> : null}
      {(plan.data?.plan ?? []).map((l) => (
        <Text key={l} testID="plan-line" style={{ color: c.ink }}>
          • {l}
        </Text>
      ))}
      {failed ? (
        <Text testID="sheet-error" accessibilityRole="alert" style={{ color: c.block }}>
          {sentence(classify(failed))}
        </Text>
      ) : null}
      {would ? (
        <>
          <Input testID="sheet-confirm" label={`Type ${a.name} to confirm`} value={typed} onChange={setTyped} />
          <Text style={{ color: c.muted, fontSize: 12 }}>Its directory is moved aside, not deleted.</Text>
        </>
      ) : null}
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button
          testID="sheet-submit"
          kind="no"
          label={removing ? 'Removing…' : 'Remove account'}
          disabled={!would || typed !== a.name || removing}
          onPress={() => remove.mutate({ account: a.name, confirm: typed }, { onSuccess: onClose })}
          style={{ flex: 1 }}
        />
        <Button testID="sheet-cancel" label="Cancel" onPress={onClose} />
      </View>
    </View>
  );
}
