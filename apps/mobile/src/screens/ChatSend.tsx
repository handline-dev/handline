// The send button and the live work line.
import { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Pressable, Text, View } from 'react-native';
import type { ChatMessage, TranscriptItem, Working } from '@handline/client';
import { turnMinutes, turnSummary } from '@handline/app-core';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';
import { MenuItem, MenuSheet } from '#src/screens/parts.tsx';

type Delivery = 'now' | 'queue';

export const LONG_PRESS_MS = 500;

/** A tap sends; while a turn runs it queues, and a long press offers Send now or Queue. */
export function SendButton(props: { disabled: boolean; choose: boolean; color?: string; onSend: (delivery?: Delivery) => void }) {
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
          borderRadius: 999,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: props.color ?? c.accent,
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

/** While the agent works, one line above the composer: three dots, what it runs now and for how long, and Stop (the interrupt) as an underlined word. Nothing when it is idle. */
export function WorkingLine(props: { working: Working; messages: ChatMessage[]; items: TranscriptItem[]; onStop?: () => void }) {
  const c = usePalette();
  const running = props.working.turn_running;
  const dots = useDots(running);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, [running]);
  if (!running) return null;
  const turn = turnSummary(props.messages, props.items, running);
  const now = turn.last ?? (props.working.line === 'Working' ? null : props.working.line);
  const text = [now ? `Running ${now}` : 'Working', turnMinutes(props.working.since)].filter(Boolean).join(' · ');
  return (
    <View
      testID="working"
      style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingHorizontal: space.md, minHeight: 44 }}
    >
      <View style={{ flexDirection: 'row', gap: space.xs, alignItems: 'center' }}>
        {dots.map((v, i) => (
          <Dot key={i} value={v} color={c.muted} />
        ))}
      </View>
      <Text numberOfLines={1} style={{ flex: 1, color: c.muted, fontSize: 12 }}>
        {text}
      </Text>
      {props.onStop && (
        <Pressable
          testID="working-stop"
          accessibilityRole="button"
          accessibilityLabel="Stop"
          onPress={props.onStop}
          style={{ minHeight: 44, justifyContent: 'center' }}
        >
          <Text style={{ color: c.ink, fontSize: 12, textDecorationLine: 'underline' }}>Stop</Text>
        </Pressable>
      )}
    </View>
  );
}
