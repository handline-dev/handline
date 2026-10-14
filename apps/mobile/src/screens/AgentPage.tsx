import { useEffect, useRef, useState } from 'react';
import { Alert, BackHandler, Pressable, ScrollView, Text, View } from 'react-native';
import { badgesOf, controlsOf, needsConfirm, type StopMode } from '@handline/app-core';
import { useAgents, usePending, useRead, useServes, useTranscript, useVerbMark, useWrite } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, AgentView, TerminalView } from '@handline/client';
import { space } from '@handline/tokens';
import { drawRows } from './termspans.tsx';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import { StateMark } from './StateMark.tsx';
import { AgentSettings } from './AgentSettings.tsx';
import { AgentStrip } from './AgentStrip.tsx';
import { AgentTasks } from './AgentTasks.tsx';
import { AgentTimeline } from './AgentTimeline.tsx';
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
  const [settings, setSettings] = useState(false);
  const goBack = useRef<() => void>(() => void store.closeAgent());
  // The Android back button goes from the agent page to the list; the list leaves the app.
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      goBack.current();
      return true;
    });
    return () => sub.remove();
  }, []);
  if (!page) return null;
  const detail = detailQuery.data;
  const state = detail?.state ?? row?.state ?? 'unknown';
  const sub = detail ?? row;
  const back = () => void store.closeAgent();
  goBack.current = back;
  return (
    <AboveKeyboard>
      <Row style={{ padding: space.md }}>
        <Pressable testID="back" onPress={back} accessibilityLabel="Back" hitSlop={12}>
          <Text style={{ color: c.accent, fontSize: 28 }}>‹</Text>
        </Pressable>
        <StateMark agent={{ state, has_dialog: row?.has_dialog ?? false, block: row?.block }} />
        <View style={{ flex: 1 }}>
          <Text style={{ color: c.ink, fontWeight: '700', fontSize: 16 }}>{name}</Text>
          <Text style={{ color: c.muted, fontSize: 12 }}>
            {[state, sub?.account && `acct ${sub.account}`, sub?.model].filter(Boolean).join(' · ')}
          </Text>
        </View>
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
  const [extra, setExtra] = useState<'tasks' | 'timeline' | null>(null); // the Tasks and Timeline tabs sit beside the store's two tabs
  const hasTasks = useServes('tasks.list').served;
  const hasTimeline = useServes('agent.timeline').served;
  const showTasks = extra === 'tasks' && hasTasks;
  const showTimeline = extra === 'timeline' && hasTimeline;
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
  const tab = (id: string, label: string, on: boolean, onPress: () => void) => (
    <Pressable
      testID={`tab-${id}`}
      accessibilityRole="tab"
      accessibilityState={{ selected: on }}
      onPress={onPress}
      style={{
        flex: 1,
        alignItems: 'center',
        padding: space.md,
        borderBottomWidth: 2,
        borderBottomColor: on ? c.accent : 'transparent',
      }}
    >
      <Text style={{ color: on ? c.accent : c.muted, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
  const pageTab = (t: 'chat' | 'terminal', label: string) =>
    tab(t, label, !showTasks && !showTimeline && current === t, () => {
      setExtra(null);
      store.setTab(t);
    });
  const busy = mark !== null;
  // A structured session is watched as a transcript in place of its terminal; its permission cards are in the transcript.
  // The daemon says which agents it runs as a structured session: those are watched as a transcript, the rest keep the terminal.
  const structured = detail.structured === true;
  return (
    <View style={{ flex: 1 }}>
      <Row style={{ paddingHorizontal: space.md, flexWrap: 'wrap' }}>
        {ctl.start && <Button label="Start" disabled={busy} onPress={() => start.mutate({ agent: name })} />}
        {ctl.resume && <Button label="Start new" disabled={busy} onPress={startNew} />}
        {(structured ? ctl.interrupt : ctl.stop) && <Button label="Interrupt" disabled={busy} onPress={() => stop('interrupt')} />}
        {ctl.stop && !structured && <Button label="Exit" disabled={busy} onPress={() => stop('exit')} />}
        {ctl.stop && <Button label="Close" disabled={busy} onPress={() => stop('close')} />}
        {ctl.stop && <Button label="Kill" disabled={busy} onPress={() => stop('kill')} kind="no" />}
        {mark && (
          <Text testID="pending-mark" style={{ color: c.muted, alignSelf: 'center' }}>
            {mark}
          </Text>
        )}
      </Row>
      <View style={{ paddingHorizontal: space.md }}>
        <AgentVerbs detail={detail} row={row} onSettings={props.onSettings} />
        {stalled && hasTimeline && !showTimeline && (
          <Button
            testID="badge-stalled"
            label={stalled.tip.toUpperCase() === stalled.label ? stalled.label : `${stalled.label}: ${stalled.tip}`}
            kind="no"
            onPress={() => setExtra('timeline')}
          />
        )}
      </View>
      <Row style={{ borderBottomWidth: 1, borderBottomColor: c.line, marginTop: space.sm }}>
        {pageTab('chat', 'Chat')}
        {pageTab('terminal', structured ? 'Transcript' : 'Terminal')}
        {hasTasks && tab('tasks', 'Tasks', showTasks, () => setExtra('tasks'))}
        {hasTimeline && tab('timeline', 'Timeline', showTimeline, () => setExtra('timeline'))}
      </Row>
      {showTimeline ? (
        <AgentTimeline agent={name} />
      ) : showTasks ? (
        <AgentTasks agent={name} />
      ) : (
        <>
          {!(structured && current === 'terminal') && <PromptSlot agent={name} prompt={detail.prompt ?? null} />}
          {current === 'chat' ? <Chat detail={detail} /> : structured ? <TranscriptTab detail={detail} /> : <Terminal detail={detail} />}
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
  const view = useSelector((s) => s.page!.view);
  if (!view) return null; // a structured agent has no terminal
  return <TerminalOf detail={props.detail} view={view} />;
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
            {lines.length ? drawRows(lines, c.termInk, c.termBg) : 'No terminal output.'}
          </Text>
        )}
      </ScrollView>
    </View>
  );
}
