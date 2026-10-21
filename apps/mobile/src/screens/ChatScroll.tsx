// The chat list's scroll: it follows its end until a hand drags it away; a sent message stands at the top with blank space under it
// for the reply to grow into; while away, new entries show a "New" button.
import { useRef, useState } from 'react';
import { Pressable, Text, type NativeScrollEvent } from 'react-native';
import type Animated from 'react-native-reanimated';
import { useSharedValue } from 'react-native-reanimated';
import { radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

export function useChatScroll() {
  const ref = useRef<Animated.ScrollView>(null);
  const follow = useRef(true);
  const [behind, setBehind] = useState(false);
  const blank = useSharedValue(0); // room under the list's end
  const viewH = useRef(0);
  const innerH = useRef(0);
  const sentAt = useRef<number | null>(null); // the list's height at a send: the first own row below it is that message
  const anchor = useRef<{ y: number; scrolled: boolean } | null>(null);
  const toEndNow = () => ref.current?.scrollToEnd({ animated: false });
  // What the viewport has left under the reply so far; at nothing, the list follows its end as usual.
  const fit = () => {
    const a = anchor.current;
    if (!a) return;
    blank.value = Math.max(0, viewH.current - (innerH.current - a.y));
    if (blank.value === 0) {
      anchor.current = null;
      follow.current = true;
      toEndNow();
    } else if (!a.scrolled) {
      a.scrolled = true;
      setTimeout(() => ref.current?.scrollTo({ y: Math.max(0, a.y - space.sm), animated: true }), 50);
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
    sent: () => (sentAt.current = innerH.current),
    /** A row was laid out; the person's own new row becomes the anchor. */
    row: (y: number, person: boolean) => {
      if (!person || sentAt.current === null || y < sentAt.current - space.lg) return;
      sentAt.current = null;
      follow.current = false;
      anchor.current = { y, scrolled: false };
      fit();
    },
    /** Jumped to a place of its own (a search hit). */
    leave,
    toEnd: () => {
      follow.current = true;
      anchor.current = null;
      blank.value = 0;
      setBehind(false);
      ref.current?.scrollToEnd({ animated: true });
    },
    scrollProps: {
      onLayout: (e: { nativeEvent: { layout: { height: number } } }) => (viewH.current = e.nativeEvent.layout.height),
      onScrollBeginDrag: leave,
      onContentSizeChange: () => follow.current && toEndNow(),
      onScroll: (e: { nativeEvent: NativeScrollEvent }) => {
        const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
        if (!follow.current && !anchor.current && contentSize.height - contentOffset.y - layoutMeasurement.height < 40) {
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
