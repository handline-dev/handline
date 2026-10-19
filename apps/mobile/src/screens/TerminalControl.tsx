// The phone terminal's hands: a line to type, the key strip, and the question when another device holds the input.
import { useEffect, useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { KEY_STRIP, fitGrid, heldByMe, heldByOther, sizeBreaks } from '@handline/app-core';
import { useWrite } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { AgentDetail, TerminalView } from '@handline/client';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { AboveKeyboard, Button, Row } from './parts.tsx';

const BYTES: Record<string, string> = { esc: '\x1b', 'ctrl-c': '\x03', enter: '\r', up: '\x1b[A', down: '\x1b[B', tab: '\t', 'shift-tab': '\x1b[Z' };
const reasonOf = (e: unknown) => (e as { reason?: string }).reason ?? (e as Error).message;
const RESIZE_MS = 150;

/** `pane` is the size in pixels of the text above: the host is asked for the grid that fits it while this device holds the input. */
export function TerminalControl(props: { detail: AgentDetail; view: TerminalView; pane: { width: number; height: number } }) {
  const c = usePalette();
  const store = useStore();
  const me = useSelector((s) => s.me);
  const { detail, view, pane } = props;
  const agent = detail.name;
  const take = useWrite('input.take', agent);
  const [text, setText] = useState('');
  const [asked, setAsked] = useState(false); // the box was tapped while another device holds the input
  const [keepSize, setKeepSize] = useState(false); // took the input as it is: the host keeps the other device's size
  const [error, setError] = useState('');
  const holder = detail.input_holders?.[0]; // the text view hears no holder notice; the agent detail does
  const mine = heldByMe(holder, me);
  const other = heldByOther(holder, me);
  const { cols, rows } = fitGrid(pane.width, pane.height);
  const fit = { cols, rows };
  const live = detail.session_open && !!view.terminal;

  useEffect(() => {
    if (!mine) setKeepSize(false);
  }, [mine]);
  useEffect(() => {
    if (!mine || keepSize || !pane.width || !pane.height || (cols === view.cols && rows === view.rows)) return;
    const t = setTimeout(() => {
      store.client.call('terminal.resize', { terminal: view.terminal, cols, rows }).catch((e) => setError(reasonOf(e))); // a refused size leaves the grid as it is
    }, RESIZE_MS);
    return () => clearTimeout(t);
  }, [store, mine, keepSize, view.terminal, view.cols, view.rows, cols, rows, pane.width, pane.height]);

  const grab = (keep: boolean) => {
    setKeepSize(keep);
    setAsked(false);
    take.mutate({ agent });
  };
  const send = (data: string) => {
    setError('');
    store.client.call('terminal.input', store.client.typedBytes(view.terminal, data, false)).catch((e) => setError(reasonOf(e)));
  };
  const onFocus = () => {
    if (other) setAsked(true);
    else if (!mine && live) grab(false);
  };
  const submit = () => {
    if (!mine) return;
    send(`${text}\r`);
    setText('');
  };
  const ask = other && live && (asked || sizeBreaks(view.cols, fit));

  return (
    <AboveKeyboard>
      {ask && (
        <View testID="terminal-ask" style={{ padding: space.md, gap: space.sm, borderTopWidth: 1, borderTopColor: c.line }}>
          <Text style={{ color: c.ink }}>
            {holder?.name} has control of this terminal. Take control, or fit the size to this device?
          </Text>
          <Row>
            <Button testID="terminal-take" label="Take control" onPress={() => grab(true)} />
            <Button testID="terminal-fit" label="Fit to this device" kind="yes" onPress={() => grab(false)} />
          </Row>
        </View>
      )}
      {!!(error || take.error) && (
        <Text style={{ color: c.block, fontSize: 12, paddingHorizontal: space.md }}>{error || reasonOf(take.error)}</Text>
      )}
      {mine && (
        <Row style={{ paddingHorizontal: space.sm, paddingTop: space.xs, flexWrap: 'wrap' }}>
          {KEY_STRIP.filter((k) => BYTES[k.key]).map((k) => (
            <Pressable key={k.key} testID={`key-${k.key}`} accessibilityLabel={k.key} hitSlop={6} onPress={() => send(BYTES[k.key]!)}
              style={{ paddingHorizontal: space.md, paddingVertical: space.xs, borderRadius: radius.md, borderWidth: 1, borderColor: c.line }}>
              <Text style={{ color: c.ink, fontFamily: 'Menlo' }}>{k.label}</Text>
            </Pressable>
          ))}
        </Row>
      )}
      <Row style={{ padding: space.sm }}>
        <TextInput
          testID="terminal-input"
          value={text}
          onChangeText={setText}
          onFocus={onFocus}
          onSubmitEditing={submit}
          editable={live}
          blurOnSubmit={false}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="send"
          placeholder={live ? 'Type to the terminal…' : 'No terminal session is open'}
          placeholderTextColor={c.muted}
          style={{ flex: 1, color: c.ink, fontFamily: 'Menlo', borderColor: c.line, borderWidth: 1, borderRadius: radius.md, padding: space.md }}
        />
        <Button testID="terminal-send" label="Send" disabled={!mine || !live} onPress={submit} />
      </Row>
    </AboveKeyboard>
  );
}
