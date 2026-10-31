// The accounts on the Usage tab (docs/pc-app-gaps.md G9): one line per account with its gauges, state and agents; one account with its alias and cap to
// edit (authored: shown at once, put back with the daemon's reason on a refusal) and, for the founder, Remove with a dry run and the name
// typed to confirm (decided: nothing changes until the daemon accepts).
import { useState, type ReactNode } from 'react';
import { FlatList, Pressable, ScrollView, Text, View } from 'react-native';
import {
  FIVE_HOURS_MS,
  STATE_WORD,
  WEEK_MS,
  agentsLine,
  gaugeOf,
  lostByAccount,
  outUntil,
  paceLine,
  runwayWords,
  span,
  stallWord,
  stateOf,
} from '@handline/app-core';
import type { Account, Window } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useAccounts, useAccountUsage, useOff, useOutlook, usePending, useServes, useWrite } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { AccountLogin } from '#src/screens/AccountLogin.tsx';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Button, EmptyState, PageHeader } from '#src/screens/parts.tsx';
import { Icon } from '#src/screens/Icon.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';
import { Input, Remove, Settings } from '#src/screens/AccountSettings.tsx';
import { Machine } from '#src/screens/Machine.tsx';

/** The topic's rows are live; the usage read adds the fields only it carries. */
function useUsageRows(): Account[] | undefined {
  const live = useAccounts().data?.accounts;
  const usage = useAccountUsage().data?.accounts;
  return live?.map((a) => ({ ...usage?.find((u) => u.name === a.name), ...a }));
}

const SPANS = { '5-hour': FIVE_HOURS_MS, week: WEEK_MS } as const;

export function Accounts(props: {
  selected: string | null;
  onSelect: (name: string | null) => void;
  footer?: ReactNode;
  onMachine?: () => void;
}) {
  return props.selected != null ? (
    <One name={props.selected} onBack={() => props.onSelect(null)} />
  ) : (
    <List onSelect={props.onSelect} footer={props.footer} onMachine={props.onMachine} />
  );
}

/** A stalled account: the word blocked, its reason, and for the founder Wake (the row loses the stall at once; a refusal puts it back and says why). */
function Stall({ a }: { a: Account }) {
  const c = usePalette();
  const founder = useStore().client.hello?.role === 'founder';
  const { served } = useServes('account.wake');
  const wake = useWrite('account.wake', a.name);
  if (!a.stalled && !wake.error) return null;
  return (
    <View testID={`stall-${a.name}`} style={{ gap: space.xs }}>
      {a.stalled ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <Icon name="block" decorative size={14} color={c.block} />
          <Text style={{ color: c.block }}>blocked</Text>
          <Text style={{ color: c.muted, flexShrink: 1, flex: 1 }}>{stallWord(a.stalled)}</Text>
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
  const style = { color: c.muted, fontSize: font.size.sm };
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
  const tone = g.tone === 'bad' ? c.block : g.tone === 'warn' ? c.wait : c.ok;
  return (
    <View testID={`gauge-${props.label}`} style={{ gap: 2 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <Text numberOfLines={1} style={{ color: c.muted, width: 64 }}>
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
        <View style={{ width: 64, flexDirection: 'row', justifyContent: 'flex-end', alignItems: 'center', gap: 4 }}>
          {g.pct >= 80 ? <Icon name={g.pct >= 100 ? 'full' : 'warn'} decorative size={14} color={tone} /> : null}
          <Text style={{ color: c.ink }}>{g.pct}%</Text>
        </View>
      </View>
      {props.detail && g.resetIn !== null ? (
        <Text style={{ color: c.muted, fontSize: font.size.sm }}>resets in {span(g.resetIn)}</Text>
      ) : null}
    </View>
  );
}

/** The one warning under an account's bars: it runs out (amber, with a glyph), or what the week loses at this pace. */
function Warning(props: { a: Account; runway?: Parameters<typeof runwayWords>[0]; lost?: number }) {
  const c = usePalette();
  const { a, runway, lost } = props;
  const runs = runway && runway.runs_out_in_seconds !== null && stateOf(a) !== 'out' ? runwayWords(runway) : null;
  if (runs)
    return (
      <View testID={`warn-${a.name}`} style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs }}>
        <Icon name="warn" decorative size={14} color={c.wait} />
        <Text style={{ color: c.wait, fontSize: font.size.sm }}>{runs}</Text>
      </View>
    );
  return lost !== undefined ? (
    <Text testID={`lost-${a.name}`} style={{ color: c.muted, fontSize: font.size.sm }}>{`${Math.round(lost)}% lost at this pace`}</Text>
  ) : null;
}

/** Under the week gauge: what the account's pace means for starting agents; nothing with pacing off or no pace. */
function PaceLine({ a }: { a: Account }) {
  const c = usePalette();
  const line = paceLine(a);
  return line ? (
    <Text testID={`pace-line-${a.name}`} style={{ color: a.pace?.state === 'over' ? c.block : c.muted, fontSize: font.size.sm }}>
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

function List(props: { onSelect: (name: string) => void; footer?: ReactNode; onMachine?: () => void }) {
  const c = usePalette();
  const query = useAccounts();
  const { served } = useServes('accounts.list');
  const rows = useUsageRows() ?? [];
  const outlook = useOutlook('7d').data;
  const lost = outlook ? lostByAccount(outlook) : [];
  return (
    <View style={{ flex: 1 }}>
      <PageHeader title="Usage" />
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
          ListFooterComponent={
            <>
              <Machine onOpen={props.onMachine} />
              {props.footer}
            </>
          }
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
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: space.sm }}>
                <Text style={{ color: c.ink, fontWeight: '600' }}>
                  {a.label}
                  {a.plan ? <Text style={{ color: c.muted, fontWeight: '400' }}>{`  ${a.plan}`}</Text> : null}
                </Text>
                {stateOf(a) === 'needs_login' ? (
                  <Text style={{ color: c.wait }}>Login needed</Text>
                ) : (
                  <Text style={{ color: c.muted }}>{agentsLine(a)}</Text>
                )}
              </View>
              {stateOf(a) === 'needs_login' ? (
                <Button
                  testID={`login-${a.name}`}
                  label="Log in"
                  onPress={() => props.onSelect(a.name)}
                  style={{ alignSelf: 'flex-start' }}
                />
              ) : (
                <>
                  <Gauge label="5-hour" w={a.five_hour} />
                  <Gauge label="week" w={a.weekly} />
                  <Warning
                    a={a}
                    runway={outlook?.runway.find((r) => r.account === a.name)}
                    lost={lost.find((l) => l.account === a.name)?.pct}
                  />
                </>
              )}
              <Stall a={a} />
            </Pressable>
          )}
        />
      </Loadable>
    </View>
  );
}

/** Founder only: a name registers the account, which then needs a login (the topic brings the row in). */
export function AddAccount() {
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
    <View style={{ gap: space.sm, alignItems: 'flex-start' }}>
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
      <Text style={{ color: c.ink, fontSize: font.size.xl, fontWeight: '700' }}>{a.label}</Text>
      {stateText(a) ? <Text style={{ color: c.muted }}>{stateText(a)}</Text> : null}
      <Gauge label="5-hour" w={a.five_hour} detail />
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
