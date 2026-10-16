// The ⋯ menu of the agent page: the verbs beyond start and stop (wake, restart, park, fold, move account) and the way to the agent's settings and schedules. Each is a decided
// write that shows its gerund until the daemon answers; the ones the bridge asks about ask first in a sheet that checks the row again on
// Confirm. Close is the page's own button; clear stall waits for the founder's decision (N4).
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { askOf, needsAsk, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import { useAccounts, useAgents, useVerbRun } from '@handline/app-data';
import type { AgentDetail, AgentView } from '@handline/client';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Sheet, useWhyNot } from './TaskActions.tsx';
import { AgentStrip } from './AgentStrip.tsx';
import { Button, Row } from './parts.tsx';

const HIDDEN: VerbId[] = ['interrupt', 'close', 'clear_stall'];

/** One of the page's own session controls, listed first in the menu. */
export type Control = { id: string; label: string; onPress: () => void; disabled?: boolean; kind?: 'no' };

export function AgentVerbs(props: {
  detail: AgentDetail;
  row: AgentView | undefined;
  onSettings: () => void;
  controls: Control[];
  /** What a control in flight says while the daemon decides. */
  note: string | null;
  /** The page's tab row; the ⋯ sits at its end. */
  tabs: React.ReactNode;
}) {
  const { detail, row } = props;
  const [open, setOpen] = useState(false);
  const [verb, setVerb] = useState<Verb | null>(null);
  const c = usePalette();
  const run = useVerbRun(detail.name);
  const verbs = row ? verbsOf(row, detail).filter((v) => !HIDDEN.includes(v.id)) : [];
  if (!row) return null;
  const pick = (v: Verb) => {
    if (needsAsk(v.id) || v.id === 'move') setVerb(v);
    else run(v);
    setOpen(false);
  };
  return (
    <View>
      <Row style={{ borderBottomWidth: 1, borderBottomColor: c.line }}>
        {props.tabs}
        <Pressable testID="verbs-open" onPress={() => setOpen(!open)} accessibilityRole="button" accessibilityLabel="More verbs" hitSlop={8} style={{ padding: space.md }}>
          <Text style={{ color: c.ink, fontSize: 18, fontWeight: '700' }}>⋯</Text>
        </Pressable>
      </Row>
      {props.note && (
        <Text testID="pending-mark" style={{ color: c.muted, padding: space.sm }}>
          {props.note}
        </Text>
      )}
      {open && (
        <View testID="verbs" style={{ gap: space.xs, padding: space.md }}>
          <AgentStrip agent={detail.name} />
          {props.controls.map((x) => (
            <Button
              key={x.id}
              testID={`control-${x.id}`}
              label={x.label}
              kind={x.kind}
              disabled={x.disabled}
              onPress={() => {
                setOpen(false);
                x.onPress();
              }}
            />
          ))}
          {verbs.map((v) => (
            <VerbButton key={v.id} v={v} onPress={() => pick(v)} />
          ))}
          <Button
            testID="verb-settings"
            label="Settings and schedules"
            onPress={() => {
              setOpen(false);
              props.onSettings();
            }}
          />
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
      {askOf(verb, detail) && <Text style={{ color: c.muted }}>{askOf(verb, detail)}</Text>}
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
