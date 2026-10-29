// The send button and the live work line.
import { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Pressable, Text, View } from 'react-native';
import type { ChatMessage, TranscriptItem, Working } from '@handline/client';
import { clockTime, turnMinutes, turnSummary } from '@handline/app-core';
import { radius, space } from '@handline/tokens';
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

const commands = (n: number) => `${n} command${n === 1 ? '' : 's'}`;

/** The slot after the last message, at least one line high so the list never jumps. While the agent works: how long, how many commands and the one now running; after the turn it stays as "Ran N commands". A tap opens the turn's tool lines. */
export function WorkingBubble(props: { working: Working; messages: ChatMessage[]; items: TranscriptItem[] }) {
  const c = usePalette();
  const running = props.working.turn_running;
  const dots = useDots(running);
  const [open, setOpen] = useState(false);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, [running]);
  const turn = turnSummary(props.messages, props.items, running);
  const now = turn.last ?? props.working.line;
  const text = running
    ? ['Working', turnMinutes(props.working.since), turn.count ? commands(turn.count) : null, now ? `now: ${now}` : null]
        .filter(Boolean)
        .join(' · ')
    : turn.count
      ? `Ran ${commands(turn.count)}`
      : null;
  return (
    <View testID="working-slot" style={{ minHeight: 36, justifyContent: 'center' }}>
      {text && (
        <Pressable
          testID={running ? 'working' : 'ran'}
          accessibilityRole="button"
          accessibilityState={{ expanded: open }}
          disabled={!turn.count}
          onPress={() => setOpen(!open)}
          style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}
        >
          {running && (
            <View
              style={{
                flexDirection: 'row',
                gap: space.xs,
                alignItems: 'center',
                paddingHorizontal: space.md,
                height: 28,
                borderRadius: radius.l,
                backgroundColor: c.panel,
              }}
            >
              {dots.map((v, i) => (
                <Dot key={i} value={v} color={c.muted} />
              ))}
            </View>
          )}
          <Text numberOfLines={1} style={{ flexShrink: 1, color: c.muted, fontSize: 12 }}>
            {text}
          </Text>
          {turn.count > 0 && <Icon name={open ? 'less' : 'more'} decorative size={14} color={c.muted} />}
        </Pressable>
      )}
      {open && (
        <View testID="turn-steps" style={{ gap: space.xs, paddingVertical: space.xs }}>
          {turn.steps.map((i) => (
            <Text key={i.id} numberOfLines={2} style={{ color: c.muted, fontSize: 12 }}>
              {[i.tool, i.text].filter(Boolean).join(' ') || i.kind} · {clockTime(i.at)}
            </Text>
          ))}
        </View>
      )}
    </View>
  );
}
