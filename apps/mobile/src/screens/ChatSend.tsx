// The send button and the working bubble, both fixed in size.
import { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Pressable, Text, View } from 'react-native';
import { radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { MenuItem, MenuSheet } from '#src/screens/parts.tsx';

type Delivery = 'now' | 'queue';

export const LONG_PRESS_MS = 500;

/** A tap sends; while a turn runs it queues, and a long press offers Send now or Queue. */
export function SendButton(props: { disabled: boolean; choose: boolean; onSend: (delivery?: Delivery) => void }) {
  const c = usePalette();
  const [asking, setAsking] = useState(false);
  const pick = (d: Delivery) => {
    setAsking(false);
    props.onSend(d);
  };
  return (
    <>
      <Pressable
        testID="chat-send"
        accessibilityRole="button"
        accessibilityLabel="Send"
        accessibilityHint={props.choose ? 'Hold to send now or queue' : undefined}
        disabled={props.disabled}
        onPress={() => props.onSend(props.choose ? 'queue' : undefined)}
        delayLongPress={LONG_PRESS_MS}
        onLongPress={props.choose ? () => setAsking(true) : undefined}
        style={{
          width: 40,
          height: 40,
          borderRadius: radius.pill,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: c.accent,
          opacity: props.disabled ? 0.35 : 1,
        }}
      >
        <Icon name="sendUp" decorative size={22} color="#fff" />
      </Pressable>
      {asking && (
        <MenuSheet testID="send-choice" onClose={() => setAsking(false)}>
          <MenuItem testID="chat-send-now" label="Send now" note="Written to the agent at once" onPress={() => pick('now')} />
          <MenuItem testID="chat-send-queue" label="Queue" note="Sent when the turn ends" onPress={() => pick('queue')} />
        </MenuSheet>
      )}
    </>
  );
}

const DOT = 6;

function Dot(props: { value: Animated.Value; color: string }) {
  return <Animated.View style={{ width: DOT, height: DOT, borderRadius: DOT / 2, backgroundColor: props.color, opacity: props.value }} />;
}

/** Three dots that rise in turn; still when the phone asks for reduced motion. */
function useDots(on: boolean): Animated.Value[] {
  const dots = useRef([0, 1, 2].map(() => new Animated.Value(0.4))).current;
  useEffect(() => {
    if (!on) return;
    let loop: Animated.CompositeAnimation | undefined;
    let gone = false;
    void AccessibilityInfo.isReduceMotionEnabled().then((reduced) => {
      if (gone || reduced) return;
      const beat = (v: Animated.Value, delay: number) =>
        Animated.sequence([
          Animated.delay(delay),
          Animated.timing(v, { toValue: 1, duration: 300, useNativeDriver: true }),
          Animated.timing(v, { toValue: 0.4, duration: 300, useNativeDriver: true }),
          Animated.delay(600 - delay),
        ]);
      loop = Animated.loop(Animated.parallel(dots.map((v, i) => beat(v, i * 150))));
      loop.start();
    });
    return () => {
      gone = true;
      loop?.stop();
    };
  }, [on, dots]);
  return dots;
}

/** A fixed-height slot after the last message, so the list never changes height. */
export function WorkingBubble(props: { working: boolean; line: string | null }) {
  const c = usePalette();
  const dots = useDots(props.working);
  return (
    <View testID="working-slot" style={{ height: 36, justifyContent: 'center' }}>
      {props.working && (
        <View testID="working" accessibilityRole="text" style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <View
            style={{ flexDirection: 'row', gap: space.xs, alignItems: 'center', paddingHorizontal: space.md, height: 28, borderRadius: radius.lg, backgroundColor: c.panel }}
          >
            {dots.map((v, i) => (
              <Dot key={i} value={v} color={c.muted} />
            ))}
          </View>
          <Text numberOfLines={1} style={{ flex: 1, color: c.muted, fontSize: 12 }}>
            {props.line ?? 'Working'}…
          </Text>
        </View>
      )}
    </View>
  );
}
