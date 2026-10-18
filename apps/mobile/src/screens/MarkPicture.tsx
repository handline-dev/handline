// The marks editor of a picked picture: the picture with no toolbar over it. A tap pins a circle, a drag draws a box, going round something
// marks an area; the memo box opens at the mark and the picture shrinks above the keyboard so the mark stays in view. Done draws the kept
// marks (red, numbered) into a PNG of the picture's own size and hands it back with the marks; a mark without words is dropped.
import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import {
  Image,
  KeyboardAvoidingView,
  Modal,
  PanResponder,
  Platform,
  SafeAreaView,
  Text,
  TextInput,
  View,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, { Path, Polyline } from 'react-native-svg';
import { captureRef } from 'react-native-view-shot';
import { MARK_COLOR, initialMarks, keptMarks, marksReducer, marksToShapes, shapesToSvg, type Mark } from '@handline/app-core';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Link } from './parts.tsx';

export interface Marked {
  blob: Blob;
  marks: Mark[];
}

export function MarkPicture(props: { uri: string; start?: Mark[]; onDone: (r: Marked | null) => void; onCancel: () => void }) {
  const c = usePalette();
  const [state, dispatch] = useReducer(marksReducer, undefined, () => ({
    ...initialMarks(),
    marks: props.start ?? [],
    next: (props.start?.length ?? 0) + 1,
  }));
  const [natural, setNatural] = useState({ w: 0, h: 0 });
  const [room, setRoom] = useState({ w: 0, h: 0 });
  const [capturing, setCapturing] = useState(false);
  const shot = useRef<View>(null);
  useEffect(
    () =>
      void Image.getSize(
        props.uri,
        (w, h) => setNatural({ w, h }),
        () => undefined,
      ),
    [props.uri],
  );

  // The picture as large as the room allows, whole.
  const fit = natural.w && room.w ? Math.min(room.w / natural.w, room.h / natural.h) : 0;
  const shown = { w: natural.w * fit, h: natural.h * fit };
  const k = fit ? 1 / fit : 1; // image pixels to a screen point
  useEffect(() => void (fit && dispatch({ type: 'scale', slop: Math.max(4, 6 * k), radius: Math.max(12, 14 * k) })), [fit, k]);
  const line = Math.max(3, Math.round(natural.w / 250));
  const { marks, open } = state;
  const kept = keptMarks(marks);

  const pan = useMemo(() => {
    const at = (e: { nativeEvent: { locationX: number; locationY: number } }) => ({
      x: e.nativeEvent.locationX * k,
      y: e.nativeEvent.locationY * k,
    });
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (e) => (dispatch({ type: 'open', id: null }), dispatch({ type: 'down', ...at(e) })),
      onPanResponderMove: (e) => dispatch({ type: 'move', ...at(e) }),
      onPanResponderRelease: (e) => dispatch({ type: 'up', ...at(e) }),
      onPanResponderTerminate: () => dispatch({ type: 'cancel' }),
    });
  }, [k]);

  const draw = shapesToSvg(marksToShapes(capturing ? kept : marks, line, !capturing), natural.w || 1, natural.h || 1);
  const draft = state.draft?.map((p) => `${p.x},${p.y}`).join(' ');
  const current = marks.find((m) => m.id === open);
  const done = async () => {
    if (!kept.length) return props.onDone(null);
    setCapturing(true);
    await new Promise((ok) => requestAnimationFrame(() => ok(null)));
    try {
      const uri = await captureRef(shot, { format: 'png', result: 'tmpfile', width: natural.w, height: natural.h });
      props.onDone({ blob: await (await fetch(uri)).blob(), marks });
    } catch {
      props.onDone({ blob: new Blob(), marks }); // the words still go; the picture as it was
    }
  };
  const pos = (m: Mark) => ({ left: m.rect.x / k, top: m.rect.y / k });
  const number = (m: Mark) => (capturing ? kept.indexOf(m) : marks.indexOf(m)) + 1;

  return (
    <Modal visible animationType="slide" onRequestClose={props.onCancel}>
      <SafeAreaView style={{ flex: 1, backgroundColor: c.bg }}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg, padding: space.md }}>
            <Link testID="mark-cancel" label="Cancel" onPress={props.onCancel} />
            <Text style={{ color: c.muted, flex: 1, textAlign: 'center' }}>
              {marks.length} {marks.length === 1 ? 'memo' : 'memos'}
            </Text>
            <Link testID="mark-undo" label="Undo" disabled={!state.past.length} onPress={() => dispatch({ type: 'undo' })} />
            <Link testID="mark-done" label="Done" onPress={() => void done()} />
          </View>
          <View
            testID="mark-room"
            style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}
            onLayout={(e: LayoutChangeEvent) => setRoom({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}
          >
            {fit > 0 && (
              <View ref={shot} collapsable={false} style={{ width: shown.w, height: shown.h }}>
                <Image source={{ uri: props.uri }} accessibilityLabel="The picture" style={{ width: shown.w, height: shown.h }} />
                <View
                  testID="mark-surface"
                  style={{ position: 'absolute', left: 0, top: 0, width: shown.w, height: shown.h }}
                  {...(capturing ? {} : pan.panHandlers)}
                >
                  <Svg width={shown.w} height={shown.h} viewBox={draw.viewBox} pointerEvents="none">
                    {draw.elements.map((el, i) =>
                      el.type === 'path' ? (
                        <Path
                          key={i}
                          d={el.d}
                          fill="none"
                          stroke={el.stroke}
                          strokeWidth={el.strokeWidth}
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      ) : null,
                    )}
                    {draft ? (
                      <Polyline
                        points={draft}
                        fill="none"
                        stroke={MARK_COLOR}
                        strokeWidth={line}
                        strokeDasharray={`${line * 2} ${line * 2}`}
                      />
                    ) : null}
                  </Svg>
                </View>
                {(capturing ? kept : marks).map((m) => (
                  <View
                    key={m.id}
                    testID={`mark-${m.id}`}
                    accessibilityLabel={`Memo ${number(m)}`}
                    onTouchEnd={() => !capturing && dispatch({ type: 'open', id: m.id })}
                    style={{
                      position: 'absolute',
                      ...pos(m),
                      width: 22,
                      height: 22,
                      marginLeft: -11,
                      marginTop: -11,
                      borderRadius: 11,
                      backgroundColor: MARK_COLOR,
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <Text style={{ color: '#fff', fontSize: 12, fontWeight: '700' }}>{number(m)}</Text>
                  </View>
                ))}
                {current && !capturing && (
                  <View
                    style={{
                      position: 'absolute',
                      // At the mark: below and to the right, flipped where that would leave the picture.
                      ...(current.rect.x / natural.w > 0.55
                        ? { right: Math.max(0, shown.w - (current.rect.x + current.rect.w) / k) }
                        : { left: (current.rect.x + current.rect.w) / k }),
                      ...(current.rect.y / natural.h > 0.6
                        ? { bottom: Math.max(0, shown.h - current.rect.y / k + 8) }
                        : { top: (current.rect.y + current.rect.h) / k + 8 }),
                      width: Math.min(220, shown.w),
                      flexDirection: 'row',
                      gap: space.xs,
                      padding: space.xs,
                      borderRadius: radius.md,
                      borderWidth: 1,
                      borderColor: MARK_COLOR,
                      backgroundColor: c.panel,
                    }}
                  >
                    <TextInput
                      testID="mark-memo"
                      autoFocus
                      multiline
                      value={current.text}
                      onChangeText={(text) => dispatch({ type: 'memo', id: current.id, text })}
                      placeholder="What is here?"
                      placeholderTextColor={c.muted}
                      accessibilityLabel="Memo"
                      style={{ flex: 1, color: c.ink, maxHeight: 80 }}
                    />
                    <Link testID="mark-remove" label="Remove" onPress={() => dispatch({ type: 'remove', id: current.id })} />
                  </View>
                )}
              </View>
            )}
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}
