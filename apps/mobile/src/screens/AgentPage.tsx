import { useEffect, useRef, useState } from 'react';
import { Alert, BackHandler, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { KEY_STRIP, controlsOf, holderLabel, needsConfirm, type StopMode } from '@handline/app-core';
import { useAgents, usePending, useRead, useServes, useVerbMark, useWrite } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, AgentView } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { drawRows } from './termspans.tsx';
import { Skeleton } from '../skeleton.tsx';
import { dotColor, usePalette } from '../theme.ts';
import { AgentSettings } from './AgentSettings.tsx';
import { AgentVerbs } from './AgentVerbs.tsx';
import { Chat } from './Chat.tsx';
import { Loadable } from './Loadable.tsx';
import { PromptSlot } from './PromptCard.tsx';
import { AboveKeyboard, Button, Row } from './parts.tsx';

/** What a start, resume, stop or interrupt in flight on this agent says while the daemon decides. */
function useAgentMark(agent: string): string | null {
  const mine = (xs: { agent: string }[]) => xs.some((p) => p.agent === agent);
  const start = usePending('session.start');
  const resume = usePending('session.restart');
  const stop = usePending('session.stop');
  const interrupt = usePending('session.interrupt');
  return mine(start) ? 'Starting…' : mine(resume) ? 'Resuming…' : mine(stop) ? 'Stopping…' : mine(interrupt) ? 'Interrupting…' : null;
}

export function AgentPage() {
  const c = usePalette();
  const store = useStore();
  const page = useSelector((s) => s.page);
  const me = useSelector((s) => s.me);
  const name = page?.agent ?? '';
  const detailQuery = useRead('agent.get', { agent: name }, { enabled: name !== '' });
  const { served } = useServes('agent.get');
  const row = useAgents().data?.agents.find((a) => a.name === name);
  const release = useWrite('input.release', name);
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
  const back = () => {
    if (detail && controlsOf(detail, me).release) release.mutate({ agent: name });
    void store.closeAgent();
  };
  goBack.current = back;
  return (
    <AboveKeyboard>
      <Row style={{ padding: space.md }}>
        <Pressable testID="back" onPress={back} accessibilityLabel="Back" hitSlop={12}>
          <Text style={{ color: c.accent, fontSize: 28 }}>‹</Text>
        </Pressable>
        <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: dotColor(c, state) }} />
        <View style={{ flex: 1 }}>
          <Text style={{ color: c.ink, fontWeight: '700', fontSize: 16 }}>{name}</Text>
          <Text style={{ color: c.muted, fontSize: 12 }}>
            {[state, sub?.account && `acct ${sub.account}`, sub?.model].filter(Boolean).join(' · ')}
          </Text>
        </View>
      </Row>
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
  const verbMark = useVerbMark(name, ['close']);
  const mark = useAgentMark(name) ?? verbMark;
  const start = useWrite('session.start', name);
  const resume = useWrite('session.restart', name);
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
  const tab = (t: 'chat' | 'terminal', label: string) => (
    <Pressable
      testID={`tab-${t}`}
      accessibilityRole="tab"
      accessibilityState={{ selected: current === t }}
      onPress={() => store.setTab(t)}
      style={{
        flex: 1,
        alignItems: 'center',
        padding: space.md,
        borderBottomWidth: 2,
        borderBottomColor: current === t ? c.accent : 'transparent',
      }}
    >
      <Text style={{ color: current === t ? c.accent : c.muted, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
  const busy = mark !== null;
  return (
    <View style={{ flex: 1 }}>
      <Row style={{ paddingHorizontal: space.md, flexWrap: 'wrap' }}>
        {ctl.start && <Button label="Start" disabled={busy} onPress={() => start.mutate({ agent: name })} />}
        {ctl.resume && <Button label="Resume" disabled={busy} onPress={() => resume.mutate({ agent: name, fresh: false })} />}
        {ctl.resume && <Button label="Start new" disabled={busy} onPress={() => start.mutate({ agent: name })} />}
        {ctl.stop && <Button label="Interrupt" disabled={busy} onPress={() => stop('interrupt')} />}
        {ctl.stop && <Button label="Exit" disabled={busy} onPress={() => stop('exit')} />}
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
      </View>
      <Row style={{ borderBottomWidth: 1, borderBottomColor: c.line, marginTop: space.sm }}>
        {tab('chat', 'Chat')}
        {tab('terminal', 'Terminal')}
      </Row>
      <PromptSlot agent={name} prompt={detail.prompt ?? null} />
      {current === 'chat' ? <Chat detail={detail} /> : <Terminal detail={detail} />}
    </View>
  );
}

function Terminal(props: { detail: AgentDetail }) {
  const c = usePalette();
  const store = useStore();
  const me = useSelector((s) => s.me);
  const view = useSelector((s) => s.page!.view);
  const lines = view.lines();
  // An open session whose snapshot has not come yet.
  const waiting = props.detail.session_open && view.frame.length === 0 && view.history.length === 0;
  const [text, setText] = useState('');
  const scroll = useRef<ScrollView>(null);
  const d = props.detail;
  const ctl = controlsOf(d, me);
  const holder = d.input_holders?.[0];
  const takeInput = useWrite('input.take', d.name);
  const releaseInput = useWrite('input.release', d.name);
  const sendInput = useWrite('session.input', d.name);
  const sendBytes = useWrite('terminal.input', d.name);
  const [refused, setRefused] = useState('');
  const sendKeys = useWrite('session.keys', d.name);
  const releasing = usePending('input.release').some((v) => v.agent === d.name);
  const taking = usePending('input.take').some((v) => v.agent === d.name);
  const take = () => {
    // Taking the input from a person at another device asks first; the daemon's own hold is not a person's.
    if (holder && holder.kind !== 'daemon' && holder.name !== me) {
      Alert.alert(`Take control from ${holderLabel(holder, me)}?`, undefined, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Take', onPress: () => takeInput.mutate({ agent: d.name }) },
      ]);
    } else takeInput.mutate({ agent: d.name });
  };
  const submit = () => {
    if (!text.trim()) return;
    // session.input is refused while the screen is unread; the bytes path is not, so it is used whenever the daemon has one.
    const sent =
      view.terminal && store.client.typesBytes
        ? sendBytes.mutateAsync(store.client.typedBytes(view.terminal, text, true))
        : sendInput.mutateAsync({ agent: d.name, text, enter: true });
    setRefused('');
    setText('');
    sent.catch((e) => {
      setText((now) => now || text); // kept for a retry
      setRefused((e as { reason?: string }).reason ?? (e as Error).message);
    });
  };
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
      {d.session_open && (
        <Row style={{ padding: space.sm, justifyContent: 'space-between' }}>
          <Text testID="holder" style={{ color: c.muted, fontSize: 12 }}>
            Input: {taking ? 'taking…' : releasing ? 'releasing…' : holderLabel(holder, me)}
          </Text>
          {ctl.take && <Button testID="take" label="Take control" disabled={taking} onPress={take} />}
          {ctl.release && <Button label="Release" disabled={releasing} onPress={() => releaseInput.mutate({ agent: d.name })} />}
        </Row>
      )}
      <Row style={{ paddingHorizontal: space.sm, gap: 4 }}>
        {KEY_STRIP.map((k) => (
          <Button
            key={k.key}
            testID={`key-${k.key}`}
            label={k.label}
            disabled={!ctl.typing}
            onPress={() => sendKeys.mutate({ agent: d.name, keys: [k.key] })}
            style={{ flex: 1, paddingHorizontal: 0, paddingVertical: space.sm }}
          />
        ))}
      </Row>
      <Row style={{ padding: space.sm }}>
        <TextInput
          testID="composer"
          value={text}
          onChangeText={setText}
          onSubmitEditing={submit}
          editable={ctl.typing}
          autoCapitalize="none"
          autoCorrect={false}
          placeholder={ctl.typing ? 'Type to the terminal' : 'Take control to type'}
          placeholderTextColor={c.muted}
          style={{ flex: 1, color: c.ink, borderColor: c.line, borderWidth: 1, borderRadius: radius.md, padding: space.md }}
        />
        <Button label="Send" onPress={submit} disabled={!ctl.typing} />
      </Row>
      {!!refused && (
        <Text testID="composer-refused" style={{ color: c.wait, fontSize: 12, paddingHorizontal: space.sm }}>
          {refused}
        </Text>
      )}
    </View>
  );
}
