// The chat list's scroll: it follows its end until a hand drags it away or a message is sent, which stands at the top over room for the reply.
import { useEffect, useRef, useState } from 'react';
import { Pressable, Text, type NativeScrollEvent, type ScrollView } from 'react-native';
import { useKeyboardState } from 'react-native-keyboard-controller';
import { radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

export function useChatScroll() {
  const ref = useRef<ScrollView>(null);
  const follow = useRef(true);
  const [behind, setBehind] = useState(false);
  const [blank, setBlank] = useState(0); // room under the list's end
  const blankNow = useRef(0);
  const viewH = useRef(0);
  const innerH = useRef(0);
  const lowest = useRef(0); // the lowest message row laid out so far
  const sentBelow = useRef<number | null>(null); // `lowest` at a send: the first own row below it is that message
  const anchor = useRef<{ y: number; scrolled: boolean } | null>(null);
  const toEndNow = () => ref.current?.scrollToEnd({ animated: false });
  // The keyboard shrinks the viewport; once that has laid out, a list at its end stays there.
  const keyboard = useKeyboardState((k) => k.height);
  useEffect(() => {
    const frame = requestAnimationFrame(() => follow.current && toEndNow());
    return () => cancelAnimationFrame(frame);
  }, [keyboard]);
  // What the viewport has left under the reply so far; at nothing, the list follows its end as usual.
  const fit = () => {
    const a = anchor.current;
    if (!a) return;
    const room = Math.max(0, viewH.current - (innerH.current - blankNow.current - a.y));
    blankNow.current = room;
    setBlank(room);
    if (room === 0) {
      anchor.current = null;
      return;
    }
    const top = Math.max(0, a.y - space.sm);
    if (!a.scrolled && innerH.current - top >= viewH.current) {
      a.scrolled = true;
      ref.current?.scrollTo({ y: top, animated: true });
    }
  };
  const leave = () => {
    follow.current = false;
    anchor.current = null;
  };
  return {
    ref,
    blank,
    behind,
    /** A message was sent: its row will stand at the top. */
    sent: () => {
      sentBelow.current = lowest.current;
    },
    /** A row was laid out; the person's own new row becomes the anchor. */
    row: (y: number, person: boolean) => {
      const below = sentBelow.current;
      if (below === null || !person || y <= below) {
        lowest.current = Math.max(lowest.current, y);
        return;
      }
      lowest.current = y;
      sentBelow.current = null;
      follow.current = false;
      anchor.current = { y, scrolled: false };
      fit();
    },
    /** Jumped to a place of its own (a search hit). */
    leave,
    toEnd: () => {
      follow.current = true;
      anchor.current = null;
      blankNow.current = 0;
      setBlank(0);
      setBehind(false);
      ref.current?.scrollToEnd({ animated: true });
    },
    scrollProps: {
      onLayout: (e: { nativeEvent: { layout: { height: number } } }) => {
        viewH.current = e.nativeEvent.layout.height;
        if (follow.current) requestAnimationFrame(toEndNow);
        fit();
      },
      onScrollBeginDrag: leave,
      onContentSizeChange: () => follow.current && toEndNow(),
      onScroll: (e: { nativeEvent: NativeScrollEvent }) => {
        const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
        if (!follow.current && !anchor.current && blankNow.current === 0 && contentSize.height - contentOffset.y - layoutMeasurement.height < 40) {
          follow.current = true;
          setBehind(false);
        }
      },
    },
    /** The list's content changed height. */
    inner: (h: number) => {
      const grew = h > innerH.current;
      innerH.current = h;
      fit();
      if (grew && !follow.current && !anchor.current) setBehind(true);
    },
  };
}

export function NewBelow(props: { onPress: () => void }) {
  const c = usePalette();
  return (
    <Pressable
      testID="chat-new"
      accessibilityRole="button"
      onPress={props.onPress}
      style={{
        position: 'absolute',
        bottom: space.sm,
        alignSelf: 'center',
        paddingVertical: space.xs,
        paddingHorizontal: space.md,
        borderRadius: radius.pill,
        backgroundColor: c.accent,
      }}
    >
      <Text style={{ color: '#fff' }}>↓ New</Text>
    </Pressable>
  );
}
