// The photo viewer: one photo, or the photos of a message, full screen on a dimmed scrim. Pinch zooms, a double tap goes between fit and 100 percent,
// a swipe down or Close leaves, a swipe sideways (or the arrows) steps. "Mark what to change" turns a drag into a box and a tap into a spot, each with
// a numbered note under the photo; Send sends the marked photo and the notes as one message. Unsent marks are asked about before they are dropped.
import { useEffect, useReducer, useRef, useState } from 'react';
import { Image, KeyboardAvoidingView, Modal, PixelRatio, Platform, Pressable, Text, View } from 'react-native';
import { captureRef } from 'react-native-view-shot';
import { initialMarks, keptMarks, markText, marksReducer } from '@handline/app-core';
import { useUploadImage } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { MarkShapes } from '#src/screens/MarkShapes.tsx';
import { Button, SafeScreen } from '#src/screens/parts.tsx';
import { useImage } from '#src/screens/ChatPictures.tsx';
import { DiscardAsk, INK, DIM, Notes, plural } from '#src/screens/PhotoNotes.tsx';
import { useZoom } from '#src/screens/PhotoZoom.ts';

export interface Shot {
  id: string;
  name: string;
}

/** Sends the words and the pictures (the ids of uploaded attachments) as one message. */
export type SendMarks = (words: string, attachments: string[]) => void;

const MAX_SIDE = 2048;
const GONE = 'This image is no longer kept.';
const FAILED = 'This image could not be loaded.';
const SCRIM = 0.96;

interface Where {
  pictures: Shot[];
  to?: string;
  onSend?: SendMarks;
  onClose: () => void;
}

export function PhotoViewer(props: Where & { start: number }) {
  const [at, setAt] = useState(Math.min(props.start, props.pictures.length - 1));
  const leaving = useRef<() => void>(props.onClose); // what Android's back button does: the open photo knows whether marks would be lost
  const pic = props.pictures[at];
  if (!pic) return null;
  return (
    <Modal visible transparent animationType="fade" onRequestClose={() => leaving.current()}>
      <Photo
        key={pic.id}
        {...props}
        pic={pic}
        at={at}
        register={(f) => void (leaving.current = f)}
        step={(by) => setAt((i) => Math.min(props.pictures.length - 1, Math.max(0, i + by)))}
      />
    </Modal>
  );
}

/** The size of the photo once drawn, and whether it failed to draw. */
function usePhotoSize(uri: string | null) {
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [bad, setBad] = useState(false);
  useEffect(() => {
    if (uri)
      Image.getSize(
        uri,
        (w, h) => setSize({ w, h }),
        () => setBad(true),
      );
  }, [uri]);
  return { size, bad, fail: () => setBad(true) };
}

function Photo(props: Where & { pic: Shot; at: number; step: (by: number) => void; register: (leave: () => void) => void }) {
  const c = usePalette();
  const { pic, pictures } = props;
  const read = useImage(pic.id, false);
  const upload = useUploadImage();
  const uri = read.state === 'ready' ? read.uri : null;
  const { size: natural, bad, fail } = usePhotoSize(uri);
  const [room, setRoom] = useState({ w: 0, h: 0 });
  const [origin, setOrigin] = useState({ x: 0, y: 0 });
  const [marking, setMarking] = useState(false);
  const [state, dispatch] = useReducer(marksReducer, undefined, initialMarks);
  const [asking, setAsking] = useState<'browse' | 'close' | null>(null); // the Discard question, and where Discard goes
  const [capturing, setCapturing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const stage = useRef<View>(null);
  const shot = useRef<View>(null);

  const fit = natural.w && room.w ? Math.min(room.w / natural.w, room.h / natural.h) : 0;
  const shown = { w: natural.w * fit, h: natural.h * fit };
  const k = fit ? 1 / fit : 1; // photo pixels to a screen point at fit
  const { marks } = state;
  const kept = keptMarks(marks);
  const line = Math.max(3, Math.round(natural.w / 250));

  const leave = () => (marking && marks.length ? setAsking('close') : props.onClose());
  const { register } = props;
  useEffect(() => void register(leave));
  const stopMarking = () => {
    setMarking(false);
    setProblem(null);
    dispatch({ type: 'clear' });
    zoom.reset();
  };

  // A point of the stage (from its middle) to a pixel of the photo, under the current zoom.
  const toPhoto = (p: { x: number; y: number }) => ({
    x: Math.min(natural.w, Math.max(0, (p.x - zoom.view.x) / zoom.view.s + shown.w / 2) * k),
    y: Math.min(natural.h, Math.max(0, (p.y - zoom.view.y) / zoom.view.s + shown.h / 2) * k),
  });
  const zoom = useZoom({
    room,
    shown,
    full: shown.w ? natural.w / PixelRatio.get() / shown.w : 1,
    origin,
    marking,
    stroke: {
      down: (p) => (dispatch({ type: 'open', id: null }), dispatch({ type: 'down', ...toPhoto(p) })),
      move: (p) => dispatch({ type: 'move', ...toPhoto(p) }),
      up: (p) => dispatch({ type: 'up', ...toPhoto(p) }),
      cancel: () => dispatch({ type: 'cancel' }),
    },
    onSwipe: (to) => (to === 'down' ? leave() : !marking && props.step(to === 'left' ? 1 : -1)),
  });
  useEffect(() => void (fit && dispatch({ type: 'scale', slop: Math.max(4, 6 * k), radius: Math.max(12, 14 * k) })), [fit, k]);

  const send = async () => {
    if (!props.onSend || !kept.length || capturing) return;
    setProblem(null);
    setCapturing(true);
    zoom.reset();
    await new Promise((ok) => requestAnimationFrame(() => ok(null)));
    try {
      let drawn: Blob | null = null;
      try {
        // captureRef sizes are in points; the device multiplies them by its pixel ratio. A JPEG keeps a photo under the attachment limit.
        const cap = Math.min(1, MAX_SIDE / Math.max(natural.w, natural.h)) / PixelRatio.get();
        const file = await captureRef(shot, {
          format: 'jpg',
          quality: 0.9,
          result: 'tmpfile',
          width: Math.round(natural.w * cap),
          height: Math.round(natural.h * cap),
        });
        drawn = await (await fetch(file)).blob();
      } catch {
        drawn = null; // the words still go, with the photo as it was
      }
      const id = drawn?.size ? (await upload(drawn, 'marked.jpg', 'image/jpeg')).id : pic.id;
      props.onSend(markText('', marks), [id]);
      props.onClose();
    } catch {
      setProblem('The notes were not sent. Try again.');
    } finally {
      setCapturing(false);
    }
  };

  const line_ = read.state === 'gone' ? GONE : read.state === 'failed' || bad ? FAILED : null;
  const fade = zoom.view.s === 1 ? Math.min(1, Math.abs(zoom.view.y) / 400) : 0;
  const canMark = !!props.onSend && read.state === 'ready' && fit > 0;
  const arrows = !marking && pictures.length > 1;

  return (
    <SafeScreen testID={`picture-full-${pic.id}`} background={`rgba(0,0,0,${SCRIM * (1 - fade * 0.6)})`}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.lg, paddingVertical: space.md }}>
          <Text numberOfLines={1} style={{ color: INK, fontWeight: '600', flexShrink: 1 }}>
            {pic.name}
          </Text>
          {pictures.length > 1 && (
            <Text testID="picture-count" style={{ color: DIM, fontSize: font.size.sm }}>
              {props.at + 1} of {pictures.length}
            </Text>
          )}
          <View style={{ flex: 1 }} />
          <Pressable testID="picture-close" accessibilityRole="button" accessibilityLabel="Close" onPress={leave} hitSlop={12}>
            <Text style={{ color: INK, fontSize: 16, fontWeight: '600' }}>Close</Text>
          </Pressable>
        </View>

        <View
          ref={stage}
          testID="photo-stage"
          style={{ flex: 1, alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}
          onLayout={(e) => {
            setRoom({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height });
            stage.current?.measureInWindow((x, y) => setOrigin({ x, y }));
          }}
          {...(line_ ? {} : zoom.handlers)}
        >
          {line_ ? (
            <Text testID="picture-line" accessibilityRole="alert" style={{ color: INK }}>
              {line_}
            </Text>
          ) : uri ? (
            <View
              style={{
                width: fit ? undefined : '100%',
                height: fit ? undefined : '100%',
                transform: [{ translateX: zoom.view.x }, { translateY: zoom.view.y }, { scale: zoom.view.s }],
              }}
            >
              <View ref={shot} collapsable={false} style={fit ? { width: shown.w, height: shown.h } : { width: '100%', height: '100%' }}>
                <Image
                  testID="picture-image"
                  accessibilityLabel={pic.name}
                  source={{ uri }}
                  resizeMode="contain"
                  style={{ width: '100%', height: '100%' }}
                  onError={fail}
                />
                {marking && fit > 0 && (
                  <MarkShapes
                    marks={marks}
                    capturing={capturing}
                    draft={state.draft}
                    natural={natural}
                    shown={shown}
                    k={k}
                    line={line}
                    onOpen={(id) => dispatch({ type: 'open', id })}
                  />
                )}
              </View>
            </View>
          ) : (
            <Text style={{ color: DIM }}>Loading…</Text>
          )}
          {arrows && props.at > 0 && <Arrow side="left" label="Previous photo" onPress={() => props.step(-1)} />}
          {arrows && props.at < pictures.length - 1 && <Arrow side="right" label="Next photo" onPress={() => props.step(1)} />}
        </View>

        {marking && <Notes marks={marks} open={state.open} dispatch={dispatch} />}

        {problem && (
          <Text testID="viewer-problem" accessibilityRole="alert" style={{ color: c.block, paddingHorizontal: space.lg }}>
            {problem}
          </Text>
        )}

        {marking ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.lg, padding: space.lg }}>
            {kept.length ? (
              <Button
                testID="marks-send"
                kind="yes"
                label={`Send ${plural(kept.length)}${props.to ? ` to ${props.to}` : ''}`}
                disabled={capturing}
                onPress={() => void send()}
                style={{ flex: 1, paddingVertical: space.lg }}
              />
            ) : (
              <Text testID="marks-hint" style={{ flex: 1, color: DIM, textAlign: 'center' }}>
                {marks.length ? 'Write what should change' : 'Drag on the photo to mark'}
              </Text>
            )}
            <Pressable
              testID="marks-cancel"
              accessibilityRole="button"
              hitSlop={12}
              onPress={() => (marks.length ? setAsking('browse') : stopMarking())}
            >
              <Text style={{ color: INK, fontSize: 16 }}>Cancel</Text>
            </Pressable>
          </View>
        ) : (
          canMark && (
            <View style={{ padding: space.lg }}>
              <Button
                testID="mark-start"
                label="Mark what to change"
                icon="edit"
                onPress={() => (zoom.reset(), setMarking(true))}
                style={{ paddingVertical: space.lg }}
              />
            </View>
          )
        )}
      </KeyboardAvoidingView>

      {asking && (
        <DiscardAsk
          count={marks.length}
          onKeep={() => setAsking(null)}
          onDiscard={() => (asking === 'close' ? props.onClose() : (setAsking(null), stopMarking()))}
        />
      )}
    </SafeScreen>
  );
}

function Arrow(props: { side: 'left' | 'right'; label: string; onPress: () => void }) {
  return (
    <Pressable
      testID={props.side === 'left' ? 'photo-prev' : 'photo-next'}
      accessibilityRole="button"
      accessibilityLabel={props.label}
      hitSlop={8}
      onPress={props.onPress}
      style={{
        position: 'absolute',
        top: '50%',
        [props.side]: space.sm,
        marginTop: -18,
        width: 36,
        height: 36,
        borderRadius: 18,
        backgroundColor: 'rgba(0,0,0,0.5)',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <Text style={{ color: INK, fontSize: 20 }}>{props.side === 'left' ? '‹' : '›'}</Text>
    </Pressable>
  );
}
