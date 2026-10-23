// The marks editor of a picked picture: the picture with no toolbar over it. A tap pins a circle, a drag draws a box, going round something
// marks an area; the memo box opens at the mark and the picture shrinks above the keyboard so the mark stays in view. Done draws the kept
// marks (red, numbered) into a JPEG of the picture's size (at most 2048 on the long side) and hands it back with the marks; a mark without words is dropped.
import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import {
  Image,
  KeyboardAvoidingView,
  Modal,
  PanResponder,
  PixelRatio,
  Platform,
  Pressable,
  Text,
  TextInput,
  View,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, { Path, Polyline } from 'react-native-svg';
import { captureRef } from 'react-native-view-shot';
import { MARK_COLOR, initialMarks, keptMarks, marksReducer, marksToShapes, shapesToSvg, type Mark } from '@handline/app-core';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Link, SafeScreen } from '#src/screens/parts.tsx';

const BAR_H = 48;

function BarButton(props: { label: string; onPress: () => void; testID: string; disabled?: boolean; bold?: boolean }) {
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole="button"
      disabled={props.disabled}
      hitSlop={8}
      onPress={props.onPress}
      style={{ opacity: props.disabled ? 0.4 : 1 }}
    >
      <Text style={{ color: '#fff', fontSize: 16, fontWeight: props.bold ? '700' : '500' }}>{props.label}</Text>
    </Pressable>
  );
}

/** The bar floats over the picture and is dragged up or down by its body; the buttons on it still take taps. */
function useDraggableBar(roomH: number) {
  const [y, setY] = useState<number>(space.md);
  const yRef = useRef(y);
  yRef.current = y;
  const start = useRef(0);
  const limit = useRef(0);
  limit.current = Math.max(0, roomH - BAR_H);
  const pan = useMemo(
    () =>
      PanResponder.create({
        onMoveShouldSetPanResponder: (_e, g) => Math.abs(g.dy) > 6 && Math.abs(g.dy) > Math.abs(g.dx),
        onPanResponderGrant: () => {
          start.current = yRef.current;
        },
        onPanResponderMove: (_e, g) => setY(Math.min(limit.current, Math.max(0, start.current + g.dy))),
      }),
    [],
  );
  return { y, pan };
}

const MAX_SIDE = 2048;
const MEMO_W = 220;

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
  const bar = useDraggableBar(room.h);
  const shot = useRef<View>(null);
  const surface = useRef<View>(null);
  const origin = useRef({ x: 0, y: 0 });
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
    // Page coordinates against the surface's own origin: the keyboard closing at the first touch moves the picture, and locationX/Y
    // would shift under the finger.
    const at = (e: { nativeEvent: { pageX: number; pageY: number } }) => ({
      x: Math.min(natural.w, Math.max(0, (e.nativeEvent.pageX - origin.current.x) * k)),
      y: Math.min(natural.h, Math.max(0, (e.nativeEvent.pageY - origin.current.y) * k)),
    });
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (e) => (dispatch({ type: 'open', id: null }), dispatch({ type: 'down', ...at(e) })),
      onPanResponderMove: (e) => dispatch({ type: 'move', ...at(e) }),
      onPanResponderRelease: (e) => dispatch({ type: 'up', ...at(e) }),
      onPanResponderTerminate: () => dispatch({ type: 'cancel' }),
    });
  }, [k, natural.w, natural.h]);

  const draw = shapesToSvg(marksToShapes(capturing ? kept : marks, line, !capturing), natural.w || 1, natural.h || 1);
  const draft = state.draft?.map((p) => `${p.x},${p.y}`).join(' ');
  const current = marks.find((m) => m.id === open);
  const done = async () => {
    if (!kept.length) return props.onDone(null);
    setCapturing(true);
    await new Promise((ok) => requestAnimationFrame(() => ok(null)));
    try {
      // captureRef sizes are in points and the device multiplies them by its pixel ratio; a JPEG keeps a photo under the attachment limit.
      const cap = Math.min(1, MAX_SIDE / Math.max(natural.w, natural.h)) / PixelRatio.get();
      const uri = await captureRef(shot, {
        format: 'jpg',
        quality: 0.9,
        result: 'tmpfile',
        width: Math.round(natural.w * cap),
        height: Math.round(natural.h * cap),
      });
      props.onDone({ blob: await (await fetch(uri)).blob(), marks });
    } catch {
      props.onDone({ blob: new Blob(), marks }); // the words still go; the picture as it was
    }
  };
  const pos = (m: Mark) => ({ left: m.rect.x / k, top: m.rect.y / k });
  const number = (m: Mark) => (capturing ? kept.indexOf(m) : marks.indexOf(m)) + 1;

  return (
    <Modal visible animationType="slide" onRequestClose={props.onCancel}>
      <SafeScreen background="#000">
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
          <View
            testID="mark-room"
            style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}
            onLayout={(e: LayoutChangeEvent) => setRoom({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}
          >
            {fit > 0 && (
              <View ref={shot} collapsable={false} style={{ width: shown.w, height: shown.h }}>
                <Image source={{ uri: props.uri }} accessibilityLabel="The picture" style={{ width: shown.w, height: shown.h }} />
                <View
                  ref={surface}
                  testID="mark-surface"
                  onLayout={() => surface.current?.measureInWindow((x, y) => (origin.current = { x, y }))}
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
                    accessibilityLabel={`Comment ${number(m)}`}
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
                    <Text style={{ color: c.onFill, fontSize: font.size.label, fontWeight: '700' }}>{number(m)}</Text>
                  </View>
                ))}
                {current && !capturing && (
                  <View
                    style={{
                      position: 'absolute',
                      // At the mark: below and to the right, flipped where that would leave the picture.
                      ...((current.rect.x + current.rect.w) / k + MEMO_W > shown.w
                        ? { right: Math.max(0, shown.w - (current.rect.x + current.rect.w) / k) }
                        : { left: (current.rect.x + current.rect.w) / k }),
                      ...(current.rect.y / natural.h > 0.6
                        ? { bottom: Math.max(0, shown.h - current.rect.y / k + 8) }
                        : { top: (current.rect.y + current.rect.h) / k + 8 }),
                      width: Math.min(MEMO_W, shown.w),
                      flexDirection: 'row',
                      gap: space.xs,
                      padding: space.xs,
                      borderRadius: radius.card,
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
                      accessibilityLabel="Comment"
                      style={{ flex: 1, color: c.ink, maxHeight: 80 }}
                    />
                    <Link testID="mark-remove" label="Remove" onPress={() => dispatch({ type: 'remove', id: current.id })} />
                  </View>
                )}
              </View>
            )}
          </View>
          <View
            testID="mark-bar"
            {...bar.pan.panHandlers}
            style={{
              position: 'absolute',
              left: space.lg,
              right: space.lg,
              top: bar.y,
              height: BAR_H,
              flexDirection: 'row',
              alignItems: 'center',
              gap: space.lg,
              paddingHorizontal: space.lg,
              borderRadius: radius.pill,
              backgroundColor: 'rgba(24,24,24,0.72)',
            }}
          >
            <BarButton testID="mark-cancel" label="Cancel" onPress={props.onCancel} />
            <Text numberOfLines={1} style={{ color: '#fff', flex: 1, textAlign: 'center' }}>
              {marks.length} {marks.length === 1 ? 'mark' : 'marks'}
            </Text>
            <BarButton testID="mark-undo" label="Undo" disabled={!state.past.length} onPress={() => dispatch({ type: 'undo' })} />
            <BarButton testID="mark-done" label="Save" bold onPress={() => void done()} />
          </View>
        </KeyboardAvoidingView>
      </SafeScreen>
    </Modal>
  );
}
