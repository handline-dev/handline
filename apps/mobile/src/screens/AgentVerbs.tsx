// The ⋯ menu of the agent page: the verbs beyond start and stop (wake, restart, park, fold, move account) and the way to the agent's settings and schedules. Each is a decided
// write that shows its gerund until the daemon answers; the ones the bridge asks about ask first in a sheet that checks the row again on
// Confirm. Close is the page's own button; clear stall waits for the founder's decision (N4). Delete agent ends the menu, for the founder only.
import { useState, type ReactNode } from 'react';
import { Text, View } from 'react-native';
import { useStore } from '@handline/app-react';
import { askOf, needsAsk, verbsOf, type Verb, type VerbId } from '@handline/app-core';
import { classify, sentence, useAccounts, useAgents, useOff, useRetireAgent, useServes, useVerbRun } from '@handline/app-data';
import type { AgentDetail, AgentView } from '@handline/client';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Sheet } from '#src/screens/TaskActions.tsx';
import { Button, MenuItem, MenuSheet, Row } from '#src/screens/parts.tsx';
import type { IconName } from '#src/screens/Icon.tsx';

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
  open: boolean;
  setOpen: (open: boolean) => void;
  /** Called once Delete agent is accepted: the agent is gone, so the page leaves. */
  onDeleted?: () => void;
  /** Rows the page puts at the top of the menu. */
  before?: ReactNode;
  /** Rarely used rows the page adds under Advanced. */
  more?: ReactNode;
  /** Stops the page offers (Exit, Close, Kill): in the menu, where a mis-tap costs less. */
  stops?: { id: string; label: string; danger: boolean }[];
  onStop?: (mode: string) => void;
  disabled?: boolean;
}) {
  const { detail, row } = props;
  const { open, setOpen } = props;
  const [verb, setVerb] = useState<Verb | null>(null);
  const [deleting, setDeleting] = useState(false);
  const founder = useStore().client.hello?.role === 'founder';
  const canDelete = useServes('agent.retire').served && founder;
  const run = useVerbRun(detail.name);
  const c = usePalette();
  const verbs = row ? verbsOf(row, detail).filter((v) => !HIDDEN.includes(v.id)) : [];
  const pick = (v: Verb) => {
    if (needsAsk(v.id) || v.id === 'move') setVerb(v);
    else run(v);
    setOpen(false);
  };
  const held = verbs.find((v) => v.id === 'force_start');
  return (
    <View>
      {held && (
        <Row style={{ flexWrap: 'wrap', paddingHorizontal: space.lg, paddingVertical: space.sm }}>
          <Text testID="held-load" style={{ color: c.wait, flex: 1 }}>
            {row?.status?.text || 'Held: the machine is over its load limit.'}
          </Text>
          <Button testID="force-start" label={held.label} icon={VERB_ICON.force_start} onPress={() => pick(held)} />
        </Row>
      )}
      {open && (
        <MenuSheet testID="verbs" onClose={() => setOpen(false)}>
          {props.before}
          {verbs.map((v) => (
            <VerbItem key={v.id} v={v} onPress={() => pick(v)} />
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
          <Advanced>
            {props.more}
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
            {canDelete && (
              <MenuItem
                testID="verb-delete"
                label="Delete agent"
                icon="dismiss"
                danger
                onPress={() => {
                  setOpen(false);
                  setDeleting(true);
                }}
              />
            )}
          </Advanced>
        </MenuSheet>
      )}
      {deleting && <DeleteSheet name={detail.name} onClose={() => setDeleting(false)} onDeleted={props.onDeleted} />}
      {verb && row && <VerbSheet row={row} detail={detail} verb={verb} run={run} onClose={() => setVerb(null)} />}
    </View>
  );
}

/** The rarely used rows, folded away until asked for. */
function Advanced(props: { children: ReactNode }) {
  const [shown, setShown] = useState(false);
  return (
    <>
      <MenuItem testID="verb-advanced" label="Advanced" icon={shown ? 'more' : 'chevronRight'} onPress={() => setShown(!shown)} />
      {shown && props.children}
    </>
  );
}

function VerbItem(props: { v: Verb; onPress: () => void }) {
  const why = useOff(props.v.method);
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

/** Delete agent: says what goes and what stays; a refusal (a running session, agents under it) keeps the sheet with the daemon's reason. */
function DeleteSheet(props: { name: string; onClose: () => void; onDeleted?: () => void }) {
  const retire = useRetireAgent(props.name);
  const [why, setWhy] = useState('');
  const [busy, setBusy] = useState(false);
  const c = usePalette();
  const go = () => {
    setBusy(true);
    setWhy('');
    retire({
      onSuccess: () => {
        props.onClose();
        props.onDeleted?.();
      },
      onError: (e) => {
        setBusy(false);
        setWhy(sentence(classify(e)));
      },
    });
  };
  return (
    <Sheet label={`Delete ${props.name}?`} submit="Delete" kind="no" busy={busy} onCancel={props.onClose} onSubmit={go} error={why || undefined}>
      <Text style={{ color: c.muted }}>
        Its team entry, schedule and keys go, and its folder is moved to the archive. Past messages, tasks, events and the git worktree stay.
      </Text>
    </Sheet>
  );
}
