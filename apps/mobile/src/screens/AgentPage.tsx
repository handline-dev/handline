import { useEffect, useRef } from 'react';
import { Alert, Pressable, ScrollView, Text, View } from 'react-native';
import { badgesOf, controlsOf, needsConfirm, shortName, type StopMode } from '@handline/app-core';
import { useAgents, usePending, useRead, useServes, useTranscript, useVerbMark, useWrite } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, AgentView, TerminalView } from '@handline/client';
import { space } from '@handline/tokens';
import { drawRows } from './termspans.tsx';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import { useHistoryValue } from '../history.tsx';
import { StateMark } from './StateMark.tsx';
import { AgentSettings } from './AgentSettings.tsx';
import { AgentStrip } from './AgentStrip.tsx';
import { AgentTasks } from './AgentTasks.tsx';
import { Icon, type IconName } from './Icon.tsx';
import { Activity } from './Activity.tsx';
import { AgentVerbs } from './AgentVerbs.tsx';
import { Chat } from './Chat.tsx';
import { Loadable } from './Loadable.tsx';
import { PromptSlot } from './PromptCard.tsx';
import { Transcript } from './Transcript.tsx';
import { AboveKeyboard, Button, Row } from './parts.tsx';

/** What a start, stop or interrupt in flight on this agent says while the daemon decides. */
function useAgentMark(agent: string): string | null {
  const mine = (xs: { agent: string }[]) => xs.some((p) => p.agent === agent);
  const start = usePending('session.start');
  const stop = usePending('session.stop');
  const interrupt = usePending('session.interrupt');
  return mine(start) ? 'Starting…' : mine(stop) ? 'Stopping…' : mine(interrupt) ? 'Interrupting…' : null;
}

export function AgentPage() {
  const c = usePalette();
  const store = useStore();
  const page = useSelector((s) => s.page);
  const name = page?.agent ?? '';
  const detailQuery = useRead('agent.get', { agent: name }, { enabled: name !== '' });
  const { served } = useServes('agent.get');
  const row = useAgents().data?.agents.find((a) => a.name === name);
  const [settings, setSettings] = useHistoryValue(`agent.settings:${name}`, false);
  if (!page) return null;
  const detail = detailQuery.data;
  const state = detail?.state ?? row?.state ?? 'unknown';
  const back = () => void store.closeAgent();
  return (
    <AboveKeyboard>
      <Row style={{ padding: space.md }}>
        <Pressable testID="back" onPress={back} accessibilityLabel="Back to Agents" hitSlop={12}>
          <Text style={{ color: c.accent, fontSize: 16 }}>‹ Agents</Text>
        </Pressable>
        <StateMark agent={{ state, has_dialog: row?.has_dialog ?? false, block: row?.block }} />
        <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '700', fontSize: 16, flex: 1 }}>
          {shortName(name, name.split('.')[0]!)}
        </Text>
      </Row>
      <AgentStrip agent={name} />
      <Loadable query={detailQuery} served={served} skeleton="agentDetail" label="the agent" empty={`${name} is gone`}>
        {detail &&
          (settings ? (
            <AgentSettings agent={name} onBack={() => setSettings(false)} />
          ) : (
            <Body detail={detail} agent={row} tab={page.tab} onSettings={() => setSettings(true)} />
          ))}
      </Loadable>
    </AboveKeyboard>
  );
}

function Body(props: { detail: AgentDetail; agent: AgentView | undefined; tab: 'chat' | 'terminal'; onSettings: () => void }) {
  const c = usePalette();
  const store = useStore();
  const me = useSelector((s) => s.me);
  const { detail, agent: row, tab: current } = props;
  const name = detail.name;
  const ctl = controlsOf(detail, me);
  const [extra, setExtra] = useHistoryValue<'tasks' | 'activity' | null>(`agent.extra:${name}`, null); // the Activity and Tasks tabs sit beside the store's two tabs
  const hasTasks = useServes('tasks.list').served;
  const showTasks = extra === 'tasks' && hasTasks;
  const hasChat = useServes('chat.messages').served;
  const showActivity = extra === 'activity' && hasChat;
  const [searching, setSearching] = useHistoryValue(`chat.search:${name}`, false);
  const onChat = !showTasks && !showActivity && current === 'chat';
  const stalled = badgesOf(detail, me).find((b) => b.kind === 'stalled');
  const verbMark = useVerbMark(name, ['close']);
  const mark = useAgentMark(name) ?? verbMark;
  const start = useWrite('session.start', name);
  const stopMode = useWrite('session.stop', name);
  const interrupt = useWrite('session.interrupt', name);
  const stop = (mode: StopMode) => {
    const run = () => (mode === 'interrupt' ? interrupt.mutate({ agent: name }) : stopMode.mutate({ agent: name, mode }));
    if (row && needsConfirm(mode, row))
      Alert.alert(`${mode === 'kill' ? 'Kill' : 'Close'} ${name}?`, undefined, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Yes', style: 'destructive', onPress: run },
      ]);
    else run();
  };
  // Starting new leaves the stored conversation behind.
  const startNew = () =>
    Alert.alert(`Start ${name} over?`, 'The stored conversation is not resumed.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Start new', style: 'destructive', onPress: () => start.mutate({ agent: name }) },
    ]);
  const tab = (id: string, label: string, icon: IconName, on: boolean, onPress: () => void) => (
    <Pressable
      testID={`tab-${id}`}
      accessibilityRole="tab"
      accessibilityState={{ selected: on }}
      onPress={onPress}
      style={{
        flexGrow: 1,
        alignItems: 'center',
        paddingHorizontal: space.sm,
        paddingVertical: space.md,
        borderBottomWidth: 2,
        borderBottomColor: on ? c.accent : 'transparent',
      }}
    >
      <Icon name={icon} decorative size={16} color={on ? c.accent : c.muted} />
      <Text numberOfLines={1} style={{ color: on ? c.accent : c.muted, fontWeight: '600', fontSize: 12 }}>
        {label}
      </Text>
    </Pressable>
  );
  const pageTab = (t: 'chat' | 'terminal', label: string) =>
    tab(t, label, t, !showTasks && !showActivity && current === t, () => {
      setExtra(null);
      store.setTab(t);
    });
  const busy = mark !== null;
  // A structured session is watched as a transcript in place of its terminal; its permission cards are in the transcript.
  // The daemon says which agents it runs as a structured session: those are watched as a transcript, the rest keep the terminal.
  const structured = detail.structured === true;
  return (
    <View style={{ flex: 1 }}>
      <View style={{ paddingHorizontal: space.md }}>
        <AgentVerbs
          detail={detail}
          row={row}
          onSettings={props.onSettings}
          lead={
            <>
              {ctl.start && <Button label="Start" disabled={busy} onPress={() => start.mutate({ agent: name })} />}
              {ctl.resume && <Button label="Start new" disabled={busy} onPress={startNew} />}
              {(structured ? ctl.interrupt : ctl.stop) && <Button label="Interrupt" disabled={busy} onPress={() => stop('interrupt')} />}
            </>
          }
          stops={[
            ...(ctl.stop && !structured ? [{ id: 'exit', label: 'Exit', danger: false }] : []),
            ...(ctl.stop ? [{ id: 'close', label: 'Close', danger: false }, { id: 'kill', label: 'Kill', danger: true }] : []),
          ]}
          onStop={(mode) => stop(mode as StopMode)}
          disabled={busy}
          trail={
            <>
              {mark && (
                <Text testID="pending-mark" style={{ color: c.muted }}>
                  {mark}
                </Text>
              )}
              {onChat && (
                <Pressable testID="search-open" accessibilityRole="button" accessibilityLabel="Search this chat" accessibilityState={{ selected: searching }} hitSlop={12} onPress={() => setSearching(!searching)}>
                  <Icon name="search" size={20} color={searching ? c.accent : c.muted} />
                </Pressable>
              )}
            </>
          }
        />
        {stalled && (
          <Text testID="badge-stalled" style={{ color: c.block }}>
            {stalled.tip.toLowerCase() === stalled.label.toLowerCase() ? stalled.label : `${stalled.label}: ${stalled.tip}`}
          </Text>
        )}
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0, borderBottomWidth: 1, borderBottomColor: c.line, marginTop: space.sm }} contentContainerStyle={{ flexGrow: 1, paddingHorizontal: space.sm }}>
        {pageTab('chat', 'Chat')}
        {pageTab('terminal', structured ? 'Transcript' : 'Terminal')}
        {hasChat && tab('activity', 'Activity', 'history', showActivity, () => setExtra('activity'))}
        {hasTasks && tab('tasks', 'Tasks', 'tasks', showTasks, () => setExtra('tasks'))}
      </ScrollView>
      {showActivity ? (
        <Activity agent={name} />
      ) : showTasks ? (
        <AgentTasks agent={name} />
      ) : (
        <>
          {!(structured && current === 'terminal') && current !== 'chat' && <PromptSlot agent={name} prompt={detail.prompt ?? null} />}
          {current === 'chat' ? <Chat detail={detail} searching={searching} /> : structured ? <TranscriptTab detail={detail} /> : <Terminal detail={detail} />}
        </>
      )}
    </View>
  );
}

/** The transcript of a structured agent; it has no terminal to fall back to, so a refused read is an error to retry. */
function TranscriptTab(props: { detail: AgentDetail }) {
  const transcript = useTranscript(props.detail.name, true);
  return (
    <>
      {/* a card the transcript cannot show (not ready) is still answerable */}
      {transcript.status !== 'ready' && <PromptSlot agent={props.detail.name} prompt={props.detail.prompt ?? null} />}
      <Transcript
        agent={props.detail.name}
        transcript={{ ...transcript, status: transcript.status === 'terminal' ? 'error' : transcript.status }}
      />
    </>
  );
}

function Terminal(props: { detail: AgentDetail }) {
  const c = usePalette();
  const store = useStore();
  const view = useSelector((s) => s.page!.view);
  const start = useWrite('session.start', props.detail.name);
  const opened = !!props.detail.terminal;
  // The agent was started from this page: its terminal now exists and is attached.
  useEffect(() => {
    if (opened && !view) void store.attachView();
  }, [opened, view, store]);
  if (view) return <TerminalOf detail={props.detail} view={view} />;
  const name = props.detail.name;
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: space.lg }}>
      <Button
        testID="terminal-start"
        label={start.isPending ? 'Starting…' : 'Start terminal'}
        kind="yes"
        disabled={start.isPending}
        onPress={() => start.mutate({ agent: name })}
      />
      {start.error ? <Text style={{ color: c.block, marginTop: space.sm }}>{(start.error as Error).message}</Text> : null}
    </View>
  );
}

function TerminalOf(props: { detail: AgentDetail; view: TerminalView }) {
  const c = usePalette();
  const store = useStore();
  const view = props.view;
  const lines = view.lines();
  // An open session whose snapshot has not come yet.
  const waiting = props.detail.session_open && view.frame.length === 0 && view.history.length === 0;
  const scroll = useRef<ScrollView>(null);
  return (
    <View style={{ flex: 1 }}>
      <ScrollView
        ref={scroll}
        testID="terminal"
        style={{ flex: 1, backgroundColor: c.termBg }}
        contentContainerStyle={{ padding: space.sm }}
        onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: false })}
      >
        {view.historyMore && <Button label="Load older" onPress={() => void store.loadOlder()} />}
        {waiting ? (
          <Skeleton shape="terminal" loading label="the terminal" />
        ) : (
          <Text testID="terminal-text" style={{ color: c.termInk, fontFamily: 'Menlo', fontSize: 11 }}>
            {drawRows(lines, c.termInk, c.termBg)}
          </Text>
        )}
      </ScrollView>
    </View>
  );
}
