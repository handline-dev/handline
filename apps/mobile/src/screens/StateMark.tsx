// An agent's state as its own shape, so no two states differ by colour alone (the web's ui/State, the same icons).
import { useEffect, useRef } from 'react';
import { AccessibilityInfo, Animated, Easing, View } from 'react-native';
import { dotOf, type Dot } from '@handline/app-core';
import type { AgentView } from '@handline/client';
import { dotColor, usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';

const MARKS: Record<Dot, { icon: IconName; label: string }> = {
  running: { icon: 'working', label: 'Working' },
  waking: { icon: 'booting', label: 'Starting' },
  waiting: { icon: 'needs', label: 'Needs you' },
  blocked: { icon: 'blocked', label: 'Blocked' },
  idle: { icon: 'idle', label: 'Idle' },
  parked: { icon: 'asleep', label: 'Parked' },
  none: { icon: 'noTerminal', label: 'Stopped' },
};

/** A working agent's glyph turns, as on the PC; still when the phone asks for reduced motion. */
function Turning({ on, children }: { on: boolean; children: React.ReactNode }) {
  const turn = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!on) return;
    let loop: Animated.CompositeAnimation | undefined;
    let gone = false;
    void AccessibilityInfo.isReduceMotionEnabled().then((reduced) => {
      if (gone || reduced) return;
      loop = Animated.loop(Animated.timing(turn, { toValue: 1, duration: 1200, easing: Easing.linear, useNativeDriver: true }));
      loop.start();
    });
    return () => {
      gone = true;
      loop?.stop();
      turn.setValue(0);
    };
  }, [on, turn]);
  const rotate = turn.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return <Animated.View style={{ transform: [{ rotate }] }}>{children}</Animated.View>;
}

export function StateMark({ agent }: { agent: Pick<AgentView, 'state' | 'has_dialog' | 'block'> }) {
  const c = usePalette();
  const dot = dotOf(agent);
  return (
    <View testID={`state-${dot}`} accessible accessibilityLabel={MARKS[dot].label} style={{ width: 16, alignItems: 'center' }}>
      <Turning on={dot === 'running'}>
        <Icon name={MARKS[dot].icon} label={MARKS[dot].label} decorative color={dotColor(c, dot)} />
      </Turning>
    </View>
  );
}
