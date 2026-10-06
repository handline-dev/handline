import { useRef, useState } from 'react';
import { Alert, Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { KEY_STRIP, controlsOf, holderLabel, needsConfirm, offered, type StopMode } from '@handline/app-core';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, Prompt } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { dotColor, usePalette } from '../theme.ts';
import { AboveKeyboard, Button, Row } from './parts.tsx';

export function AgentPage() {
  const c = usePalette();
  const store = useStore();
  const page = useSelector((s) => s.page);
  const detail = useSelector((s) => (s.page ? s.details.get(s.page.agent) : undefined));
  const agent = useSelector((s) => (s.page ? s.agents.get(s.page.agent) : undefined));
  const me = useSelector((s) => s.me);
  if (!page || !detail) return null;
  const ctl = controlsOf(detail, me);
  const back = () => {
    if (ctl.release) void store.release(page.agent);
    void store.closeAgent();
  };
  const stop = (mode: StopMode) => {
    const run = () => (mode === 'interrupt' ? store.interrupt(page.agent) : store.stopAgent(page.agent, mode));
    if (agent && needsConfirm(mode, agent))
      Alert.alert(`${mode === 'kill' ? 'Kill' : 'Close'} ${page.agent}?`, undefined, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Yes', style: 'destructive', onPress: () => void run() },
      ]);
    else void run();
  };
  const tab = (t: 'chat' | 'terminal', label: string) => (
    <Pressable
      testID={`tab-${t}`}
      accessibilityRole="tab"
      accessibilityState={{ selected: page.tab === t }}
      onPress={() => store.setTab(t)}
      style={{
        flex: 1,
        alignItems: 'center',
        padding: space.md,
        borderBottomWidth: 2,
        borderBottomColor: page.tab === t ? c.accent : 'transparent',
      }}
    >
      <Text style={{ color: page.tab === t ? c.accent : c.muted, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
  return (
    <AboveKeyboard>
      <Row style={{ padding: space.md }}>
        <Pressable testID="back" onPress={back} accessibilityLabel="Back" hitSlop={12}>
          <Text style={{ color: c.accent, fontSize: 28 }}>‹</Text>
        </Pressable>
        <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: dotColor(c, detail.state) }} />
        <View style={{ flex: 1 }}>
          <Text style={{ color: c.ink, fontWeight: '700', fontSize: 16 }}>{page.agent}</Text>
          <Text style={{ color: c.muted, fontSize: 12 }}>
            {[detail.state, detail.account && `acct ${detail.account}`, detail.model].filter(Boolean).join(' · ')}
          </Text>
        </View>
      </Row>
      <Row style={{ paddingHorizontal: space.md, flexWrap: 'wrap' }}>
        {ctl.start && <Button label="Start" onPress={() => void store.startAgent(page.agent)} />}
        {ctl.resume && <Button label="Resume" onPress={() => void store.resume(page.agent)} />}
        {ctl.resume && <Button label="Start new" onPress={() => void store.startAgent(page.agent)} />}
        {ctl.stop && <Button label="Interrupt" onPress={() => stop('interrupt')} />}
        {ctl.stop && <Button label="Exit" onPress={() => stop('exit')} />}
        {ctl.stop && <Button label="Close" onPress={() => stop('close')} />}
        {ctl.stop && <Button label="Kill" onPress={() => stop('kill')} kind="no" />}
      </Row>
      <Row style={{ borderBottomWidth: 1, borderBottomColor: c.line, marginTop: space.sm }}>
        {tab('chat', 'Chat')}
        {tab('terminal', 'Terminal')}
      </Row>
      {detail.prompt && <PermissionCard prompt={detail.prompt} />}
      {page.tab === 'chat' ? <ChatEmpty /> : <Terminal detail={detail} />}
    </AboveKeyboard>
  );
}

function ChatEmpty() {
  const c = usePalette();
  return (
    <View style={{ padding: space.xl, gap: space.sm }}>
      <Text style={{ color: c.ink, fontWeight: '600' }}>Chat is not available yet</Text>
      <Text style={{ color: c.muted }}>This daemon does not serve chat. Use the Terminal tab to talk to the agent.</Text>
    </View>
  );
}

function PermissionCard(props: { prompt: Prompt }) {
  const c = usePalette();
  const store = useStore();
  const p = props.prompt;
  const what = p.command ? `${p.tool ?? ''} ${p.command}` : p.lines.join('\n');
  return (
    <View
      testID="permission-card"
      style={{
        margin: space.md,
        padding: space.md,
        gap: space.sm,
        backgroundColor: c.panel,
        borderColor: c.wait,
        borderWidth: 1,
        borderRadius: radius.lg,
      }}
    >
      <Text style={{ color: c.ink, fontWeight: '700' }}>
        {p.kind === 'permission' ? 'Permission' : p.kind}
        {p.reason ? ` · ${p.reason}` : ''}
      </Text>
      <Text style={{ color: c.ink, fontFamily: 'Menlo', fontSize: 12 }}>{what}</Text>
      <Row>
        {offered(p).map((o) => (
          <Button
            key={o.key}
            testID={`answer-${o.key}`}
            label={o.label}
            kind={o.answer === 'yes' ? 'yes' : o.answer === 'no' ? 'no' : 'plain'}
            onPress={() => void store.answer(p, o.key)}
            style={{ flex: 1 }}
          />
        ))}
      </Row>
    </View>
  );
}

function Terminal(props: { detail: AgentDetail }) {
  const c = usePalette();
  const store = useStore();
  const me = useSelector((s) => s.me);
  const view = useSelector((s) => s.page!.view);
  const lines = view.text();
  const [text, setText] = useState('');
  const scroll = useRef<ScrollView>(null);
  const d = props.detail;
  const ctl = controlsOf(d, me);
  const holder = d.input_holders?.[0];
  const take = () => {
    // Taking the input from a person at another device asks first; the daemon's own hold is not a person's.
    if (holder && holder.kind !== 'daemon' && holder.name !== me) {
      Alert.alert(`Take control from ${holderLabel(holder, me)}?`, undefined, [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Take', onPress: () => void store.take(d.name) },
      ]);
    } else void store.take(d.name);
  };
  const submit = () => {
    if (text.trim()) {
      void store.send(d.name, text);
      setText('');
    }
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
        <Text testID="terminal-text" style={{ color: c.termInk, fontFamily: 'Menlo', fontSize: 11 }}>
          {lines.join('\n') || 'No terminal output.'}
        </Text>
      </ScrollView>
      {d.session_open && (
        <Row style={{ padding: space.sm, justifyContent: 'space-between' }}>
          <Text testID="holder" style={{ color: c.muted, fontSize: 12 }}>
            Input: {holderLabel(holder, me)}
          </Text>
          {ctl.take && <Button testID="take" label="Take control" onPress={take} />}
          {ctl.release && <Button label="Release" onPress={() => void store.release(d.name)} />}
        </Row>
      )}
      <Row style={{ paddingHorizontal: space.sm, gap: 4 }}>
        {KEY_STRIP.map((k) => (
          <Button
            key={k.key}
            testID={`key-${k.key}`}
            label={k.label}
            disabled={!ctl.typing}
            onPress={() => void store.key(d.name, k.key)}
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
    </View>
  );
}
