import { useEffect, useRef, useState } from 'react';
import { ScrollView, Text, TextInput, View } from 'react-native';
import { useWrite } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import type { InputHolder, TerminalView } from '@handline/client';
import { space } from '@handline/tokens';
import { drawRows } from './termspans.tsx';
import { Skeleton } from '../skeleton.tsx';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';

/** The daemon never sizes a terminal under this. */
const MIN_COLS = 80;
const MIN_ROWS = 24;
const CELL_W = 0.6; // a monospace cell's width in em
const LINE_H = 1.25; // line height in em
const MAX_FONT = 11;
const RESIZE_MS = 150;
const MAX_KEPT = 200; // the field is emptied past this many characters

/** What the phone shows of the terminal: a font that fits `cols` across `width`, and the grid that font gives. */
export function fitOf(width: number, height: number): { font: number; cols: number; rows: number } {
  const font = Math.min(MAX_FONT, width / (MIN_COLS * CELL_W));
  return {
    font,
    cols: Math.max(MIN_COLS, Math.floor(width / (font * CELL_W))),
    rows: Math.max(MIN_ROWS, Math.floor(height / (font * LINE_H))),
  };
}

/** The keys a phone keyboard has none of, as a terminal sends them. */
const KEYS: { id: string; label: string; data: string }[] = [
  { id: 'esc', label: 'Esc', data: '\x1b' },
  { id: 'tab', label: 'Tab', data: '\t' },
  { id: 'up', label: '↑', data: '\x1b[A' },
  { id: 'down', label: '↓', data: '\x1b[B' },
  { id: 'left', label: '←', data: '\x1b[D' },
  { id: 'right', label: '→', data: '\x1b[C' },
  { id: 'ctrl-c', label: 'Ctrl-C', data: '\x03' },
  { id: 'enter', label: 'Enter', data: '\r' },
];

export function TerminalPane(props: { agent: string; view: TerminalView; sessionOpen: boolean }) {
  const c = usePalette();
  const store = useStore();
  const { agent, view } = props;
  const take = useWrite('input.take', agent);
  const { mutate: release } = useWrite('input.release', agent);
  const resize = useWrite('terminal.resize', agent);
  const input = useWrite('terminal.input', agent);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  const [fitting, setFitting] = useState(false); // the phone sizes the terminal while it holds the input
  const field = useRef<TextInput>(null);
  const typed = useRef(''); // what the field holds, so a change says what was typed or deleted
  const scroll = useRef<ScrollView>(null);

  const holders: InputHolder[] = view.holders.filter((h) => h.kind !== 'daemon');
  const mine = holders.some((h) => h.name === store.me);
  const other = holders.find((h) => h.name !== store.me);
  const { cols: fitCols, rows: fitRows } = box ? fitOf(box.w, box.h) : { cols: 0, rows: 0 };
  const lines = view.lines();
  const waiting = props.sessionOpen && view.frame.length === 0 && view.history.length === 0;
  // The text is drawn at the font that fits the host's width, never past the phone's.
  const font = box ? Math.min(MAX_FONT, box.w / (Math.max(view.cols, MIN_COLS) * CELL_W)) : MAX_FONT;

  // Holding the input with the phone sizing: the host takes the phone's grid, and again when the phone turns.
  const { mutate: setSize } = resize;
  useEffect(() => {
    if (!mine || !fitting || !fitCols || (view.cols === fitCols && view.rows === fitRows)) return;
    const t = setTimeout(() => setSize({ terminal: view.terminal, cols: fitCols, rows: fitRows }), RESIZE_MS);
    return () => clearTimeout(t);
  }, [mine, fitting, fitCols, fitRows, view.cols, view.rows, view.terminal, setSize]);
  // Control gone to someone else: the phone no longer sizes.
  useEffect(() => {
    if (!mine) setFitting(false);
  }, [mine]);

  // The phone lets go of the input when its page closes.
  const holding = useRef(false);
  holding.current = mine;
  useEffect(() => () => void (holding.current && release({ agent })), [release, agent]);

  const failure = take.error ?? resize.error ?? input.error;
  const send = (data: string) => input.mutate(store.client.typedBytes(view.terminal, data, false));
  // Keys typed while nobody holds the input take it first, in order, and the phone then sizes the terminal.
  const taking = useRef<Promise<unknown> | null>(null);
  const type = (data: string) => {
    if (mine) return send(data);
    if (other) return;
    if (!taking.current) {
      setFitting(true);
      taking.current = take
        .mutateAsync({ agent })
        .catch(() => {}) // the write reports a refusal
        .finally(() => (taking.current = null));
    }
    void taking.current.then(() => send(data));
  };

  return (
    <View style={{ flex: 1 }}>
      {other && (
        <View testID="terminal-held" style={{ padding: space.sm, backgroundColor: c.panel, borderColor: c.line, borderBottomWidth: 1 }}>
          <Text style={{ color: c.ink, marginBottom: space.xs }}>
            Another device has control ({other.name}). Take it, or fit the size to this device?
          </Text>
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button
              testID="terminal-take"
              label="Take control"
              kind="yes"
              disabled={take.isPending}
              onPress={() => take.mutate({ agent, force: true })}
            />
            <Button
              testID="terminal-fit"
              label="Fit to this device"
              disabled={take.isPending}
              onPress={() => {
                setFitting(true);
                take.mutate({ agent, force: true });
              }}
            />
          </View>
        </View>
      )}
      <ScrollView
        ref={scroll}
        testID="terminal"
        style={{ flex: 1, backgroundColor: c.termBg }}
        contentContainerStyle={{ padding: space.sm }}
        onLayout={(e) => setBox({ w: e.nativeEvent.layout.width - 2 * space.sm, h: e.nativeEvent.layout.height - 2 * space.sm })}
        onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: false })}
        onTouchEnd={() => !other && field.current?.focus()}
      >
        {view.historyMore && <Button label="Load older" onPress={() => void store.loadOlder()} />}
        {waiting ? (
          <Skeleton shape="terminal" loading label="the terminal" />
        ) : (
          <Text testID="terminal-text" style={{ color: c.termInk, fontFamily: 'Menlo', fontSize: font, lineHeight: font * LINE_H }}>
            {drawRows(lines, c.termInk, c.termBg)}
          </Text>
        )}
      </ScrollView>
      {/* The keyboard's own keys go to the terminal as they are typed. The field is left uncontrolled: forcing it empty drops keys typed fast. */}
      <TextInput
        ref={field}
        testID="terminal-keys"
        editable={!other}
        keyboardType="visible-password" // no suggestions to replace what was typed
        autoCapitalize="none"
        autoCorrect={false}
        autoComplete="off"
        spellCheck={false}
        blurOnSubmit={false}
        onChangeText={(t) => {
          const was = typed.current;
          typed.current = t;
          if (t.length > MAX_KEPT) {
            typed.current = '';
            field.current?.clear();
          }
          let same = 0; // the keyboard may rewrite the tail: send only what differs
          while (same < was.length && same < t.length && was[same] === t[same]) same++;
          type('\x7f'.repeat(was.length - same) + t.slice(same));
        }}
        onKeyPress={(e) => {
          const key = e.nativeEvent.key;
          if (key === 'Backspace' && !typed.current) type('\x7f'); // with text in the field, the change says it
          else if (key === 'Enter') type('\r');
        }}
        style={{ position: 'absolute', width: 1, height: 1, opacity: 0 }}
      />
      {!other && (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xs, padding: space.xs, backgroundColor: c.panel }}>
          {KEYS.map((k) => (
            <Button key={k.id} testID={`terminal-key-${k.id}`} label={k.label} onPress={() => type(k.data)} style={{ paddingHorizontal: space.sm }} />
          ))}
        </View>
      )}
      {failure ? <Text style={{ color: c.block, padding: space.xs }}>{(failure as Error).message}</Text> : null}
    </View>
  );
}
