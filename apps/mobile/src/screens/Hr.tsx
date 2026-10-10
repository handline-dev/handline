// The HR list: the proposals agents raised, as on the web's HrTab. Approve, reject and withdraw are decided writes: the card changes when the
// hr topic or the refetch says so. Only the founder decides; anyone else reads the cards. "Edit settings" opens the proposal's agent's settings
// before approving, as agent.set writes.
import { useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import {
  ago,
  askedLines,
  cutError,
  DANGER,
  diffLines,
  FIELDS,
  isWaiting,
  kindOf,
  mayChange,
  ordered,
  problemWith,
  stateWord,
  textSafe,
  waitingCount,
} from '@handline/app-core';
import type { Proposal } from '@handline/client';
import { useStore } from '@handline/app-react';
import { classify, sentence, useAgentDetail, useAgents, useHr, usePending, useServes, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { Button } from './parts.tsx';
import { useWhyNot } from './TaskActions.tsx';

/** How many proposals wait on the founder; 0 while the list is not here or not served. */
export const useHrCount = (): number => waitingCount(useHr().data?.proposals ?? []);

export function Hr() {
  const c = usePalette();
  const query = useHr();
  const { served } = useServes('hr.list');
  const founder = useStore().client.hello?.role === 'founder';
  const decide = useWrite('hr.decide');
  const [all, setAll] = useState<'ask' | 'reject' | string | null>(null);
  const [why, setWhy] = useState('');
  const list = query.data?.proposals ?? [];
  const { waiting, recent } = ordered(list);
  const approveAll = async () => {
    let ok = 0;
    for (const p of waiting) {
      try {
        await decide.mutateAsync({ proposal: p.id, decision: 'approve', subject: p.subject });
        ok++;
      } catch {
        // the card shows its own refusal; the others go on
      }
    }
    setAll(ok === waiting.length ? null : `${ok} of ${waiting.length} approved`);
    void query.refetch();
  };
  const rejectAll = async () => {
    const note = why.trim();
    let ok = 0;
    for (const p of waiting) {
      try {
        await decide.mutateAsync({ proposal: p.id, decision: 'reject', note, subject: p.subject });
        ok++;
      } catch {
        // the card shows its own refusal; the others go on
      }
    }
    setWhy('');
    setAll(ok === waiting.length ? null : `${ok} of ${waiting.length} rejected`);
    void query.refetch();
  };
  return (
    <Loadable query={query} served={served} isEmpty={list.length === 0} skeleton="inbox" label="HR proposals" empty="Nothing to decide.">
      <ScrollView testID="hr-list" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
          <Text style={{ color: c.ink, fontWeight: '700', flex: 1 }}>HR{waitingCount(list) ? ` · ${waitingCount(list)} waiting` : ''}</Text>
          {founder && waiting.length > 1 && all !== 'ask' && all !== 'reject' && (
            <>
              <Button testID="hr-all" label="Approve all" onPress={() => setAll('ask')} />
              <Button testID="hr-reject-all" label="Reject all" onPress={() => setAll('reject')} />
            </>
          )}
        </View>
        {all === 'ask' ? (
          <View
            testID="hr-all-sheet"
            style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md }}
          >
            <Text style={{ color: c.ink, fontWeight: '600' }}>Approve all {waiting.length}?</Text>
            <Text style={{ color: c.muted }}>Each is applied as its card shows it; one that fails stays waiting.</Text>
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <Button testID="hr-all-go" kind="yes" label="Approve all" onPress={() => void approveAll()} style={{ flex: 1 }} />
              <Button testID="hr-all-cancel" label="Cancel" onPress={() => setAll(null)} />
            </View>
          </View>
        ) : all === 'reject' ? (
          <View
            testID="hr-reject-all-sheet"
            style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md }}
          >
            <Text style={{ color: c.ink, fontWeight: '600' }}>Reject all {waiting.length}?</Text>
            <TextInput
              testID="hr-reject-all-why"
              accessibilityLabel="Reason"
              placeholder="Reason"
              placeholderTextColor={c.muted}
              value={why}
              onChangeText={setWhy}
              style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 44 }}
            />
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <Button
                testID="hr-reject-all-go"
                kind="no"
                label="Reject all"
                disabled={!why.trim()}
                onPress={() => void rejectAll()}
                style={{ flex: 1 }}
              />
              <Button testID="hr-reject-all-cancel" label="Cancel" onPress={() => setAll(null)} />
            </View>
          </View>
        ) : all ? (
          <Text testID="hr-all-note" accessibilityRole="alert" style={{ color: c.block }}>
            {all}
          </Text>
        ) : null}
        {waiting.map((p) => (
          <Card key={p.id} p={p} decider={founder} refresh={() => void query.refetch()} />
        ))}
        {recent.length > 0 && <Text style={{ color: c.muted, fontWeight: '600' }}>Recent</Text>}
        {recent.map((p) => (
          <Card key={p.id} p={p} decider={false} refresh={() => void query.refetch()} />
        ))}
      </ScrollView>
    </Loadable>
  );
}

function Card(props: { p: Proposal; decider: boolean; refresh: () => void }) {
  const c = usePalette();
  const { p } = props;
  const decide = useWrite('hr.decide', String(p.id));
  const withdraw = useWrite('hr.withdraw', String(p.id));
  const deciding = usePending('hr.decide').some((v) => v.proposal === p.id);
  const withdrawing = usePending('hr.withdraw').some((v) => v.proposal === p.id);
  const busy = deciding || withdrawing;
  // A bundle's agent is "<N> changes" and a hire's does not exist yet: only an agent on the roster has settings to open.
  const onRoster = !!useAgents().data?.agents.some((a) => a.name === p.agent);
  const off = { decide: useWhyNot('hr.decide'), withdraw: useWhyNot('hr.withdraw'), set: useWhyNot('agent.set') };
  const [note, setNote] = useState('');
  const [mark, setMark] = useState<string | null>(null);
  const [ask, setAsk] = useState<null | 'reject' | 'withdraw' | 'settings'>(null);
  const [why, setWhy] = useState('');
  const [needWhy, setNeedWhy] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const waiting = isWaiting(p);
  const state = stateWord(p);
  const diff = diffLines(p);
  const sent = (call: Promise<unknown>, cause: string, done?: () => void) => {
    setNote('');
    setMark(cause);
    call
      .then(() => (done?.(), props.refresh()))
      .catch((e) => {
        const gate = (e as { gate?: string }).gate;
        setNote(gate === 'proposal_changed' ? 'This proposal changed' : gate === 'proposal_settled' ? 'settled' : sentence(classify(e)));
      })
      .finally(() => setMark(null));
  };
  const approve = () => sent(decide.mutateAsync({ proposal: p.id, decision: 'approve', subject: p.subject }), 'approve');
  const reject = () => {
    if (!why.trim()) return setNeedWhy(true);
    sent(
      decide.mutateAsync({ proposal: p.id, decision: 'reject', note: why.trim(), subject: p.subject }),
      'reject',
      () => (setAsk(null), setWhy('')),
    );
  };
  const locked = busy || !!mark;
  const verb = (id: string, label: string, onPress: () => void, why: string | null, kind?: 'yes' | 'no') => (
    <Button testID={`hr-${id}-${p.id}`} label={mark === id ? '…' : label} kind={kind} disabled={locked || !!why} onPress={onPress} />
  );
  return (
    <View
      testID={`hr-${p.id}`}
      style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, opacity: busy ? 0.6 : 1 }}
    >
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Text style={{ color: c.ink, fontWeight: '700', flex: 1 }}>{textSafe(p.title)}</Text>
        <Text testID={`hr-state-${p.id}`} style={{ color: c.muted }}>
          {state.word}
        </Text>
      </View>
      <Text style={{ color: c.muted, fontSize: 12 }}>
        {[p.requested_by && textSafe(p.requested_by), ago(p.created), kindOf(p)].filter(Boolean).join(' · ')}
      </Text>
      {askedLines(p).map(([k, v]) => (
        <Text key={k} style={{ color: c.ink }}>
          <Text style={{ color: c.muted }}>{k} </Text>
          {v}
        </Text>
      ))}
      {p.reason ? (
        <Text style={{ color: c.ink }}>
          <Text style={{ color: c.muted }}>why </Text>
          {textSafe(p.reason)}
        </Text>
      ) : null}
      <Pressable
        testID={`hr-diff-toggle-${p.id}`}
        accessibilityRole="button"
        accessibilityState={{ expanded: showDiff }}
        onPress={() => setShowDiff(!showDiff)}
        style={{ minHeight: 36, justifyContent: 'center' }}
      >
        <Text style={{ color: c.ink, textDecorationLine: 'underline' }}>
          {showDiff ? 'Hide diff' : `Show diff (${diff.length} change${diff.length === 1 ? '' : 's'})`}
        </Text>
      </Pressable>
      {showDiff && (
        <View
          testID={`hr-diff-${p.id}`}
          accessibilityLabel="What approving does"
          style={{ padding: space.sm, backgroundColor: c.panel, borderRadius: radius.md }}
        >
          {diff.map((l, i) => (
            <Text key={i} style={{ fontFamily: 'Courier', fontSize: 12, color: l.mark === '-' ? c.block : l.mark === '+' ? c.run : c.ink }}>
              {l.text}
            </Text>
          ))}
        </View>
      )}
      {p.state === 'apply_failed' && (
        <Text style={{ color: c.block }}>{p.error ? cutError(textSafe(p.error)) : 'the change was not applied'}</Text>
      )}
      {note === 'settled' ? (
        <Text style={{ color: c.muted }}>already {p.state}</Text>
      ) : note ? (
        <Text testID={`hr-note-${p.id}`} accessibilityRole="alert" style={{ color: c.block }}>
          {note}
        </Text>
      ) : null}
      {waiting && !props.decider && <Text style={{ color: c.muted }}>Decide on a computer.</Text>}
      {waiting && props.decider && (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
          {verb('approve', p.state === 'apply_failed' ? 'Retry' : 'Approve', approve, off.decide, 'yes')}
          {verb('reject', 'Reject', () => setAsk('reject'), off.decide)}
          {onRoster && verb('settings', 'Edit settings', () => setAsk('settings'), off.set)}
          {verb('withdraw', 'Withdraw', () => setAsk('withdraw'), off.withdraw)}
        </View>
      )}
      {waiting && props.decider && (off.decide ?? null) && <Text style={{ color: c.muted, fontSize: 12 }}>{off.decide}</Text>}
      {ask === 'reject' && (
        <View testID="hr-reject-sheet" style={{ gap: space.sm }}>
          <TextInput
            testID="hr-reject-why"
            accessibilityLabel="Why reject"
            placeholder="Why reject"
            placeholderTextColor={c.muted}
            value={why}
            onChangeText={(v) => (setWhy(v), setNeedWhy(false))}
            style={{
              color: c.ink,
              borderWidth: 1,
              borderColor: needWhy ? c.block : c.line,
              borderRadius: radius.md,
              padding: space.md,
              minHeight: 44,
            }}
          />
          {needWhy && (
            <Text testID="hr-reject-error" accessibilityRole="alert" style={{ color: c.block }}>
              A reason is required.
            </Text>
          )}
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button testID="hr-reject-go" kind="no" label="Reject" disabled={locked} onPress={reject} style={{ flex: 1 }} />
            <Button testID="hr-reject-cancel" label="Cancel" onPress={() => setAsk(null)} />
          </View>
        </View>
      )}
      {ask === 'withdraw' && (
        <View testID="hr-withdraw-sheet" style={{ gap: space.sm }}>
          <Text style={{ color: c.muted }}>Takes the proposal back; whoever raised it can raise it again.</Text>
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button
              testID="hr-withdraw-go"
              kind="no"
              label="Withdraw"
              disabled={locked}
              onPress={() => sent(withdraw.mutateAsync({ proposal: p.id }), 'withdraw', () => setAsk(null))}
              style={{ flex: 1 }}
            />
            <Button testID="hr-withdraw-cancel" label="Cancel" onPress={() => setAsk(null)} />
          </View>
        </View>
      )}
      {ask === 'settings' && <AgentSettings agent={p.agent!} onClose={() => setAsk(null)} />}
    </View>
  );
}

/** One agent's settings, saved together with one agent.set: only what changed is sent, and a refusal keeps the sheet and what was chosen. */
function AgentSettings(props: { agent: string; onClose: () => void }) {
  const c = usePalette();
  const { agent } = props;
  const q = useAgentDetail(agent);
  const role = useStore().client.hello?.role;
  const set = useWrite('agent.set', agent);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState('');
  const [danger, setDanger] = useState<string | null>(null);
  const stored = q.data?.settings as Record<string, string | undefined> | undefined;
  if (!stored) return <Text style={{ color: c.muted }}>{q.isError ? sentence(classify(q.error)) : 'Loading settings…'}</Text>;
  const fields = FIELDS.filter((f) => f.kind !== 'sites');
  const value = (k: string) => draft[k] ?? stored[k] ?? '';
  const changed = fields.filter((f) => draft[f.key] !== undefined && draft[f.key] !== (stored[f.key] ?? ''));
  const risky = changed.find((f) => DANGER[f.key]?.to === draft[f.key]);
  const save = () => {
    for (const f of changed) {
      const bad = problemWith(f, draft[f.key]!);
      if (bad) return setProblem(`${f.label}: ${bad}`);
    }
    setProblem('');
    if (!changed.length) return props.onClose();
    if (risky && danger !== risky.key) return setDanger(risky.key);
    const values = Object.fromEntries(
      changed.map((f) => [f.key, f.kind === 'number' ? String(Number(draft[f.key])) : draft[f.key]!.trim()]),
    );
    set.mutateAsync({ agent, values }).then(props.onClose, () => {});
  };
  const chip = (k: string, o: string) => (
    <Pressable
      key={o}
      testID={`hs-${k}-${o}`}
      accessibilityRole="radio"
      accessibilityState={{ selected: value(k) === o }}
      onPress={() => (setDraft({ ...draft, [k]: o }), setDanger(null))}
      style={{
        borderWidth: value(k) === o ? 2 : 1,
        borderColor: value(k) === o ? c.ink : c.line,
        borderRadius: radius.pill,
        paddingVertical: space.sm,
        paddingHorizontal: space.md,
      }}
    >
      <Text style={{ color: c.ink, fontWeight: value(k) === o ? '700' : '400' }}>{value(k) === o ? `✓ ${o}` : o}</Text>
    </Pressable>
  );
  const err = problem || (set.isError ? sentence(classify(set.error)) : '');
  return (
    <View
      testID="hr-settings"
      style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, backgroundColor: c.panel }}
    >
      <Text style={{ color: c.ink, fontWeight: '600' }}>settings of {agent}</Text>
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Button
          testID="hs-save"
          kind={danger ? 'no' : 'yes'}
          label={danger ? DANGER[danger as keyof typeof DANGER]!.go : 'Save'}
          disabled={set.isPending}
          onPress={save}
          style={{ flex: 1 }}
        />
        <Button testID="hs-cancel" label="Cancel" onPress={props.onClose} />
      </View>
      {danger && DANGER[danger as keyof typeof DANGER] && (
        <Text testID="hs-danger" style={{ color: c.block }}>
          {DANGER[danger as keyof typeof DANGER]!.title}: {DANGER[danger as keyof typeof DANGER]!.text}
        </Text>
      )}
      {err ? (
        <Text testID="hs-error" accessibilityRole="alert" style={{ color: c.block }}>
          {err}
        </Text>
      ) : null}
      {fields.map((f) => {
        const open = mayChange(f.key, role);
        const options = f.kind === 'switch' ? ['on', 'off'] : f.kind === 'select' ? f.options! : null;
        return (
          <View key={f.key} style={{ gap: space.xs, opacity: open ? 1 : 0.5 }}>
            <Text style={{ color: c.muted, fontWeight: '600' }}>
              {f.label}
              {f.unit ? ` (${f.unit.split(',')[0]})` : ''}
            </Text>
            {options ? (
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>{open && options.map((o) => chip(f.key, o))}</View>
            ) : (
              <TextInput
                testID={`hs-${f.key}`}
                accessibilityLabel={f.label}
                editable={open}
                value={value(f.key)}
                onChangeText={(v) => (setDraft({ ...draft, [f.key]: v }), setProblem(''))}
                style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 44 }}
              />
            )}
          </View>
        );
      })}
    </View>
  );
}
