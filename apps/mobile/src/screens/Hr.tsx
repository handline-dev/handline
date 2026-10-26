// The HR list: the proposals agents raised, as on the web's HrTab. Approve, reject and withdraw are decided writes: the card changes when the
// hr topic or the refetch says so. A paired person decides; an agent cannot. "Edit settings" opens the proposal's agent's settings
// before approving, as agent.set writes.
import { useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import {
  ago,
  askedLines,
  cutError,
  DANGER,
  changesToggle,
  diffLines,
  plainText,
  FIELDS,
  heldLine,
  isHeld,
  isWaiting,
  kindOf,
  mayChange,
  ordered,
  splitStale,
  problemWith,
  stateWord,
  textSafe,
  waitingCount,
} from '@handline/app-core';
import type { Proposal } from '@handline/client';
import { useOnce, useStore } from '@handline/app-react';
import { classify, sentence, useAgentDetail, useAgents, useHr, useOff, usePending, useServes, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { Button, Card as Panel } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

export function Hr() {
  const c = usePalette();
  const query = useHr();
  const { served } = useServes('hr.list');
  const { served: canDecide } = useServes('hr.decide');
  const decide = useWrite('hr.decide');
  const [all, setAll] = useState<'ask' | 'reject' | string | null>(null);
  const [why, setWhy] = useState('');
  const [going, once] = useOnce();
  const list = query.data?.proposals ?? [];
  const { waiting, recent: settledAndHeld } = ordered(list);
  const recent = settledAndHeld.filter((p) => !isHeld(p));
  const { fresh, stale } = splitStale(waiting);
  // Approved and held for a running turn: shown with the waiting ones, but the verbs and the count leave them out.
  const held = list.filter(isHeld).sort((a, b) => Date.parse(a.created) - Date.parse(b.created));
  const decideAll = (decision: 'approve' | 'reject') =>
    once(async () => {
      const note = decision === 'reject' ? why.trim() : undefined;
      let ok = 0;
      for (const p of waiting) {
        try {
          await decide.mutateAsync({ proposal: p.id, decision, note, subject: p.subject });
          ok++;
        } catch {
          // the card shows its own refusal; the others go on
        }
      }
      setWhy('');
      setAll(ok === waiting.length ? null : `${ok} of ${waiting.length} ${decision}d`);
      void query.refetch();
    });
  return (
    <Loadable query={query} served={served} isEmpty={list.length === 0} skeleton="inbox" label="HR proposals" empty="Nothing to decide.">
      <ScrollView testID="hr-list" contentContainerStyle={{ padding: space.lg, gap: space.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
          <Text style={{ color: c.ink, fontWeight: '700', flex: 1 }}>HR{waitingCount(list) ? ` · ${waitingCount(list)} waiting` : ''}</Text>
          {canDecide && waiting.length > 1 && all !== 'ask' && all !== 'reject' && (
            <>
              <Button testID="hr-all" label="Approve all" onPress={() => setAll('ask')} />
              <Button testID="hr-reject-all" label="Reject all" onPress={() => setAll('reject')} />
            </>
          )}
        </View>
        {all === 'ask' ? (
          <Sheet
            id="hr-all"
            label={`Approve all ${waiting.length}?`}
            submit="Approve all"
            busy={going}
            onCancel={() => !going && setAll(null)}
            onSubmit={() => void decideAll('approve')}
          >
            <Text style={{ color: c.muted }}>Each is applied as its card shows it; one that fails stays waiting.</Text>
          </Sheet>
        ) : all === 'reject' ? (
          <Sheet
            id="hr-reject-all"
            label={`Reject all ${waiting.length}?`}
            submit="Reject all"
            kind="no"
            busy={going || !why.trim()}
            onCancel={() => !going && setAll(null)}
            onSubmit={() => void decideAll('reject')}
          >
            <TextInput
              testID="hr-reject-all-why"
              accessibilityLabel="Reason"
              placeholder="Reason"
              placeholderTextColor={c.muted}
              value={why}
              onChangeText={setWhy}
              style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, padding: space.md, minHeight: 44 }}
            />
          </Sheet>
        ) : all ? (
          <Text testID="hr-all-note" accessibilityRole="alert" style={{ color: c.block }}>
            {all}
          </Text>
        ) : null}
        {fresh.map((p) => (
          <Card key={p.id} p={p} decider={canDecide} refresh={() => void query.refetch()} />
        ))}
        {held.map((p) => (
          <Card key={p.id} p={p} decider={false} refresh={() => void query.refetch()} />
        ))}
        {stale.length > 0 && <Text style={{ color: c.muted, fontWeight: '600' }}>Stale · no answer for 14 days</Text>}
        {stale.map((p) => (
          <Card key={p.id} p={p} decider={canDecide} refresh={() => void query.refetch()} />
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
  const off = { decide: useOff('hr.decide'), withdraw: useOff('hr.withdraw'), set: useOff('agent.set') };
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
  const verb = (id: string, label: string, onPress: () => void, why: string | undefined, kind?: 'yes' | 'no') => (
    <Button testID={`hr-${id}-${p.id}`} label={mark === id ? '…' : label} kind={kind} disabled={locked || !!why} onPress={onPress} />
  );
  return (
    <View
      testID={`hr-${p.id}`}
      style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, opacity: busy ? 0.6 : 1 }}
    >
      <View style={{ flexDirection: 'row', gap: space.sm }}>
        <Text style={{ color: c.ink, fontWeight: '700', flex: 1 }}>{textSafe(p.title)}</Text>
        <Text testID={`hr-state-${p.id}`} style={{ color: c.muted }}>
          {state.word}
        </Text>
      </View>
      <Text style={{ color: c.muted, fontSize: font.size.label }}>
        {[p.requested_by && (p.requested_by === 'human' ? 'by you' : textSafe(p.requested_by)), ago(p.created), kindOf(p)]
          .filter(Boolean)
          .join(' · ')}
      </Text>
      {isHeld(p) && <Text style={{ color: c.muted }}>{heldLine(p)}</Text>}
      {askedLines(p).map(([k, v]) => (
        <Text key={k} style={{ color: c.ink }}>
          <Text style={{ color: c.muted }}>{k} </Text>
          {v}
        </Text>
      ))}
      {p.reason ? (
        <Text style={{ color: c.ink }}>
          <Text style={{ color: c.muted }}>why </Text>
          {plainText(p.reason)}
        </Text>
      ) : null}
      <Pressable
        testID={`hr-diff-toggle-${p.id}`}
        accessibilityRole="button"
        accessibilityState={{ expanded: showDiff }}
        onPress={() => setShowDiff(!showDiff)}
        style={{ minHeight: 36, justifyContent: 'center' }}
      >
        <Text style={{ color: c.ink, textDecorationLine: 'underline' }}>{changesToggle(p, showDiff)}</Text>
      </Pressable>
      {showDiff && (
        <View
          testID={`hr-diff-${p.id}`}
          accessibilityLabel="What approving does"
          style={{ padding: space.sm, backgroundColor: c.panel, borderRadius: radius.card }}
        >
          {diff.map((l, i) => (
            <Text
              key={i}
              style={{ fontFamily: 'Courier', fontSize: font.size.label, color: l.mark === '-' ? c.block : l.mark === '+' ? c.run : c.ink }}
            >
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
      {waiting && props.decider && (off.decide ?? null) && <Text style={{ color: c.muted, fontSize: font.size.label }}>{off.decide}</Text>}
      {ask === 'reject' && (
        <Sheet
          id="hr-reject"
          label="Reject this proposal?"
          submit="Reject"
          kind="no"
          busy={locked}
          onCancel={() => setAsk(null)}
          onSubmit={reject}
          error={needWhy ? 'A reason is required.' : undefined}
        >
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
              borderRadius: radius.card,
              padding: space.md,
              minHeight: 44,
            }}
          />
        </Sheet>
      )}
      {ask === 'withdraw' && (
        <Sheet
          id="hr-withdraw"
          label="Withdraw this proposal?"
          submit="Withdraw"
          kind="no"
          busy={locked}
          onCancel={() => setAsk(null)}
          onSubmit={() => sent(withdraw.mutateAsync({ proposal: p.id }), 'withdraw', () => setAsk(null))}
        >
          <Text style={{ color: c.muted }}>Takes the proposal back; whoever raised it can raise it again.</Text>
        </Sheet>
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
    <Panel testID="hr-settings">
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
                style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, padding: space.md, minHeight: 44 }}
              />
            )}
          </View>
        );
      })}
    </Panel>
  );
}
