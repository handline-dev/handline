// The ⋯ menu of the agent page: the verbs beyond start and stop (wake, restart, park, fold, move account), as on the web. Each is a decided
// write that shows its gerund until the daemon answers; the ones the bridge asks about ask first in a sheet that checks the row again on
// Confirm. Close is the page's own button; clear stall waits for the founder's decision (N4).
import { useState } from 'react';
import { Text, View } from 'react-native';
import { VERBS, callOf, needsAsk, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import { useAccounts, useAgents, usePending, useWrite } from '@handline/app-data';
import type { AgentDetail, AgentView } from '@handline/client';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Sheet, useWhyNot } from './TaskActions.tsx';
import { Button, Row } from './parts.tsx';

const HIDDEN: VerbId[] = ['close', 'clear_stall'];

/** Starts a verb's write. */
function useVerbRun(agent: string) {
  const wake = useWrite('session.wake', agent);
  const restart = useWrite('session.restart', agent);
  const park = useWrite('session.park', agent);
  const fold = useWrite('agent.fold', agent);
  const move = useWrite('agent.move', agent);
  return (v: Verb, account?: string) => {
    const p = callOf(v, agent, account).params as never;
    if (v.method === 'session.wake') wake.mutate(p);
    else if (v.method === 'session.restart') restart.mutate(p);
    else if (v.method === 'session.park') park.mutate(p);
    else if (v.method === 'agent.fold') fold.mutate(p);
    else if (v.method === 'agent.move') move.mutate(p);
  };
}

/** The gerund of the menu verb in flight on this agent ("parking…"); null when none. */
export function useVerbMark(agent: string): string | null {
  const mine = <T extends { agent: string }>(l: T[]) => l.some((v) => v.agent === agent);
  const hits: [boolean, VerbId][] = [
    [mine(usePending('session.wake')), 'wake'],
    [mine(usePending('session.restart')), 'restart'],
    [mine(usePending('session.park')), 'park'],
    [mine(usePending('agent.fold')), 'fold'],
    [mine(usePending('agent.move')), 'move'],
  ];
  const hit = hits.find(([on]) => on);
  return hit ? VERBS[hit[1]].gerund : null;
}

export function AgentVerbs(props: { detail: AgentDetail; row: AgentView | undefined }) {
  const { detail, row } = props;
  const [open, setOpen] = useState(false);
  const [verb, setVerb] = useState<Verb | null>(null);
  const run = useVerbRun(detail.name);
  const verbs = row ? verbsOf(row, detail).filter((v) => !HIDDEN.includes(v.id)) : [];
  if (!row) return null;
  const pick = (v: Verb) => {
    if (needsAsk(v.id) || v.id === 'move') setVerb(v);
    else run(v);
    setOpen(false);
  };
  return (
    <View style={{ gap: space.sm }}>
      <Button testID="verbs-open" label="⋯" onPress={() => setOpen(!open)} accessibilityLabel="More verbs" />
      {open && (
        <View testID="verbs" style={{ gap: space.xs }}>
          {verbs.map((v) => (
            <VerbButton key={v.id} v={v} onPress={() => pick(v)} />
          ))}
        </View>
      )}
      {verb && <VerbSheet row={row} detail={detail} verb={verb} run={run} onClose={() => setVerb(null)} />}
    </View>
  );
}

function VerbButton(props: { v: Verb; onPress: () => void }) {
  const c = usePalette();
  const why = useWhyNot(props.v.method);
  return (
    <View>
      <Button testID={`verb-${props.v.id}`} label={props.v.label} disabled={!!why} onPress={props.onPress} />
      {why && <Text style={{ color: c.muted, fontSize: 11 }}>{why}</Text>}
    </View>
  );
}

function VerbSheet(props: {
  row: AgentView;
  detail: AgentDetail;
  verb: Verb;
  run: (v: Verb, account?: string) => void;
  onClose: () => void;
}) {
  const c = usePalette();
  const { row, detail, verb } = props;
  // The daemon's accounts when it lists them (an unused account is often the one to move to); else the ones agents already use.
  const listed = useAccounts().data?.accounts.map((x) => x.name);
  const used = (useAgents().data?.agents ?? []).map((x) => x.account);
  const accounts = [...new Set(listed ?? used)].filter((x): x is string => !!x && x !== row.account);
  const [account, setAccount] = useState('');
  const [changed, setChanged] = useState(false);
  const move = verb.id === 'move';
  const confirm = () => {
    if (!verbsOf(row, detail).some((v) => v.id === verb.id)) return setChanged(true); // the row changed while the sheet was open
    props.run(verb, account);
    props.onClose();
  };
  if (changed) return <Sheet label="It changed while you looked." submit="Close" onCancel={props.onClose} onSubmit={props.onClose} />;
  return (
    <Sheet
      label={`${verb.label} ${row.name}?`}
      submit={move ? 'Move' : 'Confirm'}
      busy={move && !account}
      onCancel={props.onClose}
      onSubmit={confirm}
    >
      {verb.ask && <Text style={{ color: c.muted }}>{verb.ask}</Text>}
      {move && (
        <Row style={{ flexWrap: 'wrap' }}>
          {accounts.map((x) => (
            <Button
              key={x}
              testID={`account-${x}`}
              label={account === x ? `✓ ${x}` : x}
              kind={account === x ? 'yes' : 'plain'}
              onPress={() => setAccount(x)}
            />
          ))}
        </Row>
      )}
    </Sheet>
  );
}
