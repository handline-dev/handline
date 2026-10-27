// The accounts on the Usage tab (docs/pc-app-gaps.md G9): one line per account with its gauges, state and agents; one account with its alias and cap to
// edit (authored: shown at once, put back with the daemon's reason on a refusal) and, for the founder, Remove with a dry run and the name
// typed to confirm (decided: nothing changes until the daemon accepts).
import { useEffect, useState, type ReactNode } from 'react';
import { FlatList, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { FIVE_HOURS_MS, STATE_WORD, agentsLine, paceLine, WEEK_MS, gaugeOf, outUntil, span, stateOf } from '@handline/app-core';
import type { Account, Window } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useAccounts, useAccountUsage, useOff, usePending, useServes, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { AccountLogin } from '#src/screens/AccountLogin.tsx';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Button, EmptyState, PageHeader } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

/** The topic's rows are live; the usage read adds the fields only it carries. */
function useUsageRows(): Account[] | undefined {
  const live = useAccounts().data?.accounts;
  const usage = useAccountUsage().data?.accounts;
  return live?.map((a) => ({ ...usage?.find((u) => u.name === a.name), ...a }));
}

const SPANS = { '5 hours': FIVE_HOURS_MS, week: WEEK_MS } as const;

export function Accounts(props: { selected: string | null; onSelect: (name: string | null) => void; footer?: ReactNode }) {
  return props.selected != null ? (
    <One name={props.selected} onBack={() => props.onSelect(null)} />
  ) : (
    <List onSelect={props.onSelect} footer={props.footer} />
  );
}

/** A stalled account: STALLED and its reason, and for the founder Wake (the row loses the stall at once; a refusal puts it back and says why). */
function Stall({ a }: { a: Account }) {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const { served } = useServes('account.wake');
  const wake = useWrite('account.wake', a.name);
  if (!a.stalled && !wake.error) return null;
  return (
    <View testID={`stall-${a.name}`} style={{ gap: space.xs }}>
      {a.stalled ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, flexWrap: 'wrap' }}>
          <Text style={{ color: c.block, fontWeight: '700' }}>STALLED</Text>
          <Text style={{ color: c.muted, flexShrink: 1 }}>{a.stalled.reason}</Text>
          {founder && served ? (
            <Button
              testID={`wake-${a.name}`}
              label={wake.isPending ? 'Waking…' : 'Wake'}
              disabled={wake.isPending}
              onPress={() => wake.mutate({ account: a.name })}
            />
          ) : null}
        </View>
      ) : null}
      {wake.error ? (
        <Text testID={`wake-error-${a.name}`} accessibilityRole="alert" style={{ color: c.block }}>
          {sentence(classify(wake.error))}
        </Text>
      ) : null}
    </View>
  );
}

const dash = (v: string | number | null | undefined) => (v === null || v === undefined || v === '' ? '—' : String(v));

/** What `hl account list` adds to the gauges: plan and runway, and whether another agent may start and why not; `working` adds the agents count a list row already shows in its head. Nothing until the usage read arrives. */
function UsageLines({ a, working }: { a: Account; working?: boolean }) {
  const c = usePalette();
  const style = { color: c.muted, fontSize: font.size.label };
  if (a.plan === undefined && a.runway === undefined && a.assignable === undefined && !(working && a.agents_running !== undefined))
    return null;
  return (
    <View testID={`usage-${a.name}`} style={{ gap: 2 }}>
      {a.plan !== undefined || a.runway !== undefined ? (
        <Text style={style}>{`plan ${dash(a.plan)} · runway ${dash(a.runway)}`}</Text>
      ) : null}
      {a.assignable === undefined ? null : (
        <Text style={style}>{a.assignable ? 'can take another agent' : `cannot take another agent: ${dash(a.assign_reason)}`}</Text>
      )}
      {working && a.agents_running !== undefined ? <Text style={style}>{agentsLine(a)}</Text> : null}
    </View>
  );
}

function Gauge(props: { label: keyof typeof SPANS; w: Window | null; detail?: boolean }) {
  const c = usePalette();
  if (!props.w) return <Text style={{ color: c.muted }}>{props.label} no reading</Text>;
  const g = gaugeOf(props.w, SPANS[props.label], Date.now());
  const tone = g.tone === 'bad' ? c.block : g.tone === 'warn' ? c.wait : c.run;
  return (
    <View testID={`gauge-${props.label}`} style={{ gap: 2 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <Text numberOfLines={1} style={{ color: c.muted, width: 76 }}>
          {props.label}
        </Text>
        <View style={{ flex: 1, height: 6, borderRadius: 3, backgroundColor: c.line }}>
          <View style={{ width: `${g.pct}%`, height: 6, borderRadius: 3, backgroundColor: tone }} />
          {g.through !== null ? (
            <View
              testID={`pace-${props.label}`}
              style={{ position: 'absolute', left: `${g.through}%`, top: -3, width: 2, height: 12, backgroundColor: c.ink }}
            />
          ) : null}
        </View>
        <Text style={{ color: c.ink, width: 48, textAlign: 'right' }}>
          {g.pct}%{g.pct >= 100 ? ' full' : g.pct >= 80 ? ' !' : ''}
        </Text>
      </View>
      {props.detail && g.resetIn !== null ? (
        <Text style={{ color: c.muted, fontSize: font.size.label }}>resets in {span(g.resetIn)}</Text>
      ) : null}
    </View>
  );
}

/** Under the week gauge: what the account's pace means for starting agents; nothing with pacing off or no pace. */
function PaceLine({ a }: { a: Account }) {
  const c = usePalette();
  const line = paceLine(a);
  return line ? (
    <Text testID={`pace-line-${a.name}`} style={{ color: a.pace?.state === 'over' ? c.block : c.muted, fontSize: font.size.label }}>
      {line}
    </Text>
  ) : null;
}

/** The state as a word, never only a colour. */
function stateText(a: Account): string {
  const s = stateOf(a);
  const until = s === 'out' ? outUntil(a) : null;
  return until ? `out until ${span(Date.parse(until) - Date.now())} from now` : s === 'in_use' ? '' : STATE_WORD[s];
}

function List(props: { onSelect: (name: string) => void; footer?: ReactNode }) {
  const c = usePalette();
  const query = useAccounts();
  const { served } = useServes('accounts.list');
  const rows = useUsageRows() ?? [];
  return (
    <View style={{ flex: 1 }}>
      <PageHeader title="Usage" actions={<Add />} />
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
          ListFooterComponent={<>{props.footer}</>}
          renderItem={({ item: a }) => (
            <Pressable
              testID={`account-${a.name}`}
              onPress={() => props.onSelect(a.name)}
              style={({ pressed }) => ({
                padding: space.lg,
                gap: space.xs,
                borderTopWidth: 1,
                borderTopColor: c.line,
                backgroundColor: pressed ? c.press : undefined,
              })}
            >
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Text style={{ color: c.ink, fontWeight: '600' }}>{a.label}</Text>
                <Text style={{ color: c.muted }}>{agentsLine(a)}</Text>
              </View>
              {stateText(a) ? <Text style={{ color: c.muted, fontSize: font.size.label }}>{stateText(a)}</Text> : null}
              {stateOf(a) === 'needs_login' ? (
                <Button
                  testID={`login-${a.name}`}
                  label="Log in"
                  onPress={() => props.onSelect(a.name)}
                  style={{ alignSelf: 'flex-start' }}
                />
              ) : (
                <>
                  <Gauge label="5 hours" w={a.five_hour} />
                  <Gauge label="week" w={a.weekly} />
                </>
              )}
              <PaceLine a={a} />
              <Stall a={a} />
              <UsageLines a={a} />
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
  const off = useOff('account.add');
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
        <Sheet
          label="Add an account"
          submit={adding ? 'Adding…' : 'Add account'}
          busy={!name.trim() || adding}
          onCancel={close}
          onSubmit={() => add.mutate({ account: name.trim() }, { onSuccess: close })}
          error={add.error ? sentence(classify(add.error)) : undefined}
        >
          <Input testID="sheet-name" label="Name" value={name} onChange={setName} />
        </Sheet>
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
      style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, padding: space.md, minHeight: 44 }}
    />
  );
}

function One({ name, onBack }: { name: string; onBack: () => void }) {
  const query = useAccounts();
  const { served } = useServes('accounts.list');
  const a = useUsageRows()?.find((x) => x.name === name);
  return (
    <View style={{ flex: 1 }}>
      <PageHeader back={{ label: 'Accounts', onPress: onBack, testID: 'account-back' }} />
      <Loadable query={query} served={served} skeleton="accounts" label="accounts" empty="This account no longer exists.">
        {a ? <Body a={a} /> : query.data ? <EmptyState text="This account no longer exists." /> : null}
      </Loadable>
    </View>
  );
}

function Body({ a }: { a: Account }) {
  const c = usePalette();
  return (
    <ScrollView testID="account-detail" keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: space.lg, gap: space.lg }}>
      <Text style={{ color: c.ink, fontSize: font.size.page, fontWeight: '700' }}>{a.label}</Text>
      {stateText(a) ? <Text style={{ color: c.muted }}>{stateText(a)}</Text> : null}
      <Gauge label="5 hours" w={a.five_hour} detail />
      <Gauge label="week" w={a.weekly} detail />
      <PaceLine a={a} />
      <Stall a={a} />
      <UsageLines a={a} working />
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
  useEffect(() => setAlias(was.alias), [was.alias]);
  useEffect(() => setCap(was.cap), [was.cap]);
  useEffect(() => setEnd(was.end), [was.end]);
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

function Remove({ a }: { a: Account }) {
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
          <Text style={{ color: c.muted, fontSize: font.size.label }}>Its directory is moved aside, not deleted.</Text>
        </>
      ) : null}
    </Sheet>
  );
}
