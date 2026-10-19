// The ⋯ menu of the agent page: the verbs beyond start and stop (wake, restart, park, fold, move account) and the way to the agent's settings and schedules. Each is a decided
// write that shows its gerund until the daemon answers; the ones the bridge asks about ask first in a sheet that checks the row again on
// Confirm. Close is the page's own button; clear stall waits for the founder's decision (N4).
import { useState, type ReactNode } from 'react';
import { Text, View } from 'react-native';
import { askOf, needsAsk, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import { useAccounts, useAgents, useVerbRun } from '@handline/app-data';
import type { AgentDetail, AgentView } from '@handline/client';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Sheet, useWhyNot } from './TaskActions.tsx';
import { Button, MenuItem, MenuSheet, Row } from './parts.tsx';
import type { IconName } from './Icon.tsx';

const VERB_ICON: Record<VerbId, IconName> = {
  interrupt: 'cancel',
  close: 'close',
  restart: 'retry',
  restart_fresh: 'session',
  park: 'asleep',
  fold: 'closed',
  move: 'accounts',
  clear_stall: 'refresh',
  force_start: 'play',
};
const STOP_ICON: Record<string, IconName> = { exit: 'back', close: 'close', kill: 'full' };

const HIDDEN: VerbId[] = ['interrupt', 'close', 'clear_stall'];

export function AgentVerbs(props: {
  detail: AgentDetail;
  row: AgentView | undefined;
  onSettings: () => void;
  /** The buttons beside the ⋯, and what sits at the far end of the row. */
  lead?: ReactNode;
  trail?: ReactNode;
  /** Stops the page offers (Exit, Close, Kill): in the menu, where a mis-tap costs less. */
  stops?: { id: string; label: string; danger: boolean }[];
  onStop?: (mode: string) => void;
  disabled?: boolean;
}) {
  const { detail, row } = props;
  const [open, setOpen] = useState(false);
  const [verb, setVerb] = useState<Verb | null>(null);
  const run = useVerbRun(detail.name);
  const c = usePalette();
  const verbs = row ? verbsOf(row, detail).filter((v) => !HIDDEN.includes(v.id)) : [];
  if (!row)
    return (
      <Row style={{ flexWrap: 'wrap' }}>
        {props.lead}
        {props.trail}
      </Row>
    );
  const pick = (v: Verb) => {
    if (needsAsk(v.id) || v.id === 'move') setVerb(v);
    else run(v);
    setOpen(false);
  };
  const held = verbs.find((v) => v.id === 'force_start');
  return (
    <View style={{ gap: space.sm }}>
      {held && (
        <Row style={{ flexWrap: 'wrap' }}>
          <Text testID="held-load" style={{ color: c.wait, flex: 1 }}>
            Held: the machine is over its load limit.
          </Text>
          <Button testID="force-start" label={held.label} icon={VERB_ICON.force_start} onPress={() => pick(held)} />
        </Row>
      )}
      <Row style={{ flexWrap: 'wrap' }}>
        {props.lead}
        <Button testID="verbs-open" label="" icon="menu" onPress={() => setOpen(!open)} accessibilityLabel="More" />
        <View style={{ flex: 1 }} />
        {props.trail}
      </Row>
      {open && (
        <MenuSheet testID="verbs" onClose={() => setOpen(false)}>
          {verbs.map((v) => (
            <VerbItem key={v.id} v={v} onPress={() => pick(v)} />
          ))}
          {props.stops?.map((s) => (
            <MenuItem
              key={s.id}
              testID={`stop-${s.id}`}
              label={s.label}
              icon={STOP_ICON[s.id]}
              danger={s.danger}
              disabled={props.disabled}
              onPress={() => {
                setOpen(false);
                props.onStop?.(s.id);
              }}
            />
          ))}
          <MenuItem
            testID="verb-settings"
            label="Settings and schedules"
            icon="settings"
            onPress={() => {
              setOpen(false);
              props.onSettings();
            }}
          />
        </MenuSheet>
      )}
      {verb && <VerbSheet row={row} detail={detail} verb={verb} run={run} onClose={() => setVerb(null)} />}
    </View>
  );
}

function VerbItem(props: { v: Verb; onPress: () => void }) {
  const why = useWhyNot(props.v.method);
  return <MenuItem testID={`verb-${props.v.id}`} label={props.v.label} icon={VERB_ICON[props.v.id]} disabled={!!why} note={why} onPress={props.onPress} />;
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
