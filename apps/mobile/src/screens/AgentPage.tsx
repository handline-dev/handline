import { useEffect, useState } from 'react';
import { Alert, Text, View } from 'react-native';
import { badgesOf, controlsOf, needsConfirm, type StopMode } from '@handline/app-core';
import { useAgents, usePending, useRead, useServes, useTranscript, useVerbMark, useWrite } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, AgentView } from '@handline/client';
import { font, space } from '@handline/tokens';
import { TerminalPane } from '#src/screens/TerminalPane.tsx';
import { usePalette } from '#src/theme.ts';
import { useHistoryValue } from '#src/history.tsx';
import { AgentSettings } from '#src/screens/AgentSettings.tsx';
import { AgentDock, AgentLabel, useKeyboardUp, useRememberAgent } from '#src/screens/AgentDock.tsx';
import { AgentStrip } from '#src/screens/AgentStrip.tsx';
import { AgentFile } from '#src/screens/AgentFile.tsx';
import { AgentPreview } from '#src/screens/AgentPreview.tsx';
import { AgentTasks } from '#src/screens/AgentTasks.tsx';
import type { IconName } from '#src/screens/Icon.tsx';
import { Activity } from '#src/screens/Activity.tsx';
import { AgentVerbs } from '#src/screens/AgentVerbs.tsx';
import { Chat } from '#src/screens/Chat.tsx';
import { Loadable } from '#src/screens/Loadable.tsx';
import { PromptSlot } from '#src/screens/PromptCard.tsx';
import { Transcript } from '#src/screens/Transcript.tsx';
import { AboveKeyboard, Button, MenuItem } from '#src/screens/parts.tsx';

/** What a start, stop or interrupt in flight on this agent says while the daemon decides. */
function useAgentMark(agent: string): string | null {
  const mine = (xs: { agent: string }[]) => xs.some((p) => p.agent === agent);
  const start = usePending('session.start');
  const stop = usePending('session.stop');
  const interrupt = usePending('session.interrupt');
  return mine(start) ? 'Starting…' : mine(stop) ? 'Stopping…' : mine(interrupt) ? 'Interrupting…' : null;
}

export function AgentPage() {
  const store = useStore();
  const page = useSelector((s) => s.page);
  const name = page?.agent ?? '';
  const detailQuery = useRead('agent.get', { agent: name }, { enabled: name !== '' });
  const { served } = useServes('agent.get');
  const row = useAgents().data?.agents.find((a) => a.name === name);
  const [settings, setSettings] = useHistoryValue(`agent.settings:${name}`, false);
  const [menu, setMenu] = useState(false);
  const keyboardUp = useKeyboardUp();
  useRememberAgent(name);
  if (!page) return null;
  const detail = detailQuery.data;
  return (
    <AboveKeyboard>
      <Loadable query={detailQuery} served={served} skeleton="agentDetail" label="the agent" empty={`${name} is gone`}>
        {detail &&
          (settings ? (
            <AgentSettings agent={name} onBack={() => setSettings(false)} />
          ) : (
            <Body detail={detail} agent={row} tab={page.tab} menu={menu} setMenu={setMenu} onSettings={() => setSettings(true)} />
          ))}
      </Loadable>
      {!keyboardUp && <AgentDock agent={name} onMenu={() => setMenu(true)} onBack={() => void store.closeAgent()} />}
    </AboveKeyboard>
  );
}

function Body(props: { detail: AgentDetail; agent: AgentView | undefined; tab: 'chat' | 'terminal'; menu: boolean; setMenu: (open: boolean) => void; onSettings: () => void }) {
  const c = usePalette();
  const store = useStore();
  const me = useSelector((s) => s.me);
  const { detail, agent: row, tab: current } = props;
  const name = detail.name;
  const ctl = controlsOf(detail, me);
  const [extra, setExtra] = useHistoryValue<'tasks' | 'activity' | 'file' | 'preview' | null>(`agent.extra:${name}`, null); // the Activity and Tasks tabs sit beside the store's two tabs
  const hasTasks = useServes('tasks.list').served;
  const showTasks = extra === 'tasks' && hasTasks;
  const hasChat = useServes('chat.messages').served;
  const showActivity = extra === 'activity' && hasChat;
  const hasFile = useServes('file.read').served && !!detail.worktree;
  const showFile = extra === 'file' && hasFile;
  const showPreview = extra === 'preview';
  const [searching, setSearching] = useHistoryValue(`chat.search:${name}`, false);
  const onChat = !showTasks && !showActivity && !showFile && !showPreview && current === 'chat';
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
    <MenuItem
      testID={`tab-${id}`}
      label={label}
      icon={icon}
      on={on}
      onPress={() => {
        props.setMenu(false);
        onPress();
      }}
    />
  );
  const pageTab = (t: 'chat' | 'terminal', label: string) =>
    tab(t, label, t, !showTasks && !showActivity && !showFile && !showPreview && current === t, () => {
      setExtra(null);
      store.setTab(t);
    });
  const busy = mark !== null;
  // A structured session is watched as a transcript in place of its terminal; its permission cards are in the transcript.
  // The daemon says which agents it runs as a structured session: those are watched as a transcript, the rest keep the terminal.
  const structured = detail.structured === true;
  const views = (
    <>
      <AgentLabel agent={name} />
      <AgentStrip agent={name} />
      {pageTab('chat', 'Chat')}
      {pageTab('terminal', structured ? 'Transcript' : 'Terminal')}
      {hasChat && tab('activity', 'Activity', 'history', showActivity, () => setExtra('activity'))}
      {hasFile && tab('file', 'File', 'note', showFile, () => setExtra('file'))}
      {tab('preview', 'Preview', 'open', showPreview, () => setExtra('preview'))}
      {hasTasks && tab('tasks', 'Tasks', 'tasks', showTasks, () => setExtra('tasks'))}
      {onChat && (
        <MenuItem
          testID="search-open"
          label={searching ? 'Close search' : 'Search this chat'}
          icon="search"
          onPress={() => {
            props.setMenu(false);
            setSearching(!searching);
          }}
        />
      )}
      {ctl.start && <MenuItem label="Start" icon="play" disabled={busy} onPress={() => (props.setMenu(false), start.mutate({ agent: name }))} />}
      {ctl.resume && <MenuItem label="Start new" icon="session" disabled={busy} onPress={() => (props.setMenu(false), startNew())} />}
      {(structured ? ctl.interrupt : ctl.stop) && <MenuItem label="Interrupt" icon="cancel" disabled={busy} onPress={() => (props.setMenu(false), stop('interrupt'))} />}
    </>
  );
  const note = [stalled && (stalled.tip.toLowerCase() === stalled.label.toLowerCase() ? stalled.label : `${stalled.label}: ${stalled.tip}`)].filter(Boolean);
  return (
    <View style={{ flex: 1 }}>
      <AgentVerbs
        detail={detail}
        row={row}
        open={props.menu}
        setOpen={props.setMenu}
        onDeleted={() => void store.closeAgent()}
        onSettings={props.onSettings}
        before={views}
        stops={[
          ...(ctl.stop && !structured ? [{ id: 'exit', label: 'Exit', danger: false }] : []),
          ...(ctl.stop ? [{ id: 'close', label: 'Close', danger: false }, { id: 'kill', label: 'Kill', danger: true }] : []),
        ]}
        onStop={(mode) => stop(mode as StopMode)}
        disabled={busy}
      />
      {showFile ? (
        <>
          <AgentLabel agent={name} />
          <AgentFile agent={name} />
        </>
      ) : showPreview ? (
        <AgentPreview agent={name} />
      ) : showActivity ? (
        <Activity agent={name} />
      ) : showTasks ? (
        <AgentTasks agent={name} />
      ) : (
        <>
          {!(structured && current === 'terminal') && current !== 'chat' && <PromptSlot agent={name} prompt={detail.prompt ?? null} />}
          {current === 'chat' ? <Chat detail={detail} searching={searching} /> : structured ? <TranscriptTab detail={detail} /> : <Terminal detail={detail} />}
        </>
      )}
      {mark && (
        <Text testID="pending-mark" style={{ color: c.muted, paddingHorizontal: space.md, paddingVertical: space.xs, fontSize: font.size.label }}>
          {mark}
        </Text>
      )}
      {note.length > 0 && (
        <Text testID="badge-stalled" style={{ color: c.block, paddingHorizontal: space.md, paddingVertical: space.xs, fontSize: font.size.label }}>
          {note.join(' · ')}
        </Text>
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
  if (view)
    return (
      <>
        <AgentLabel agent={props.detail.name} />
        <TerminalPane agent={props.detail.name} view={view} sessionOpen={props.detail.session_open} />
      </>
    );
  const name = props.detail.name;
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: space.lg }}>
      <Button
        testID="terminal-start"
        label={start.isPending ? 'Starting…' : 'Start terminal'}
        kind="yes"
        disabled={start.isPending || start.isSuccess}
        onPress={() => start.mutate({ agent: name })}
      />
      {start.error ? <Text style={{ color: c.block, marginTop: space.sm }}>{(start.error as Error).message}</Text> : null}
    </View>
  );
}
