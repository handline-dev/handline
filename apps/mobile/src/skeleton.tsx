// The loading placeholder on the phone: the shared spec of `@handline/app-data` drawn with Views, shown by the same gate
// (not before 150 ms, not for less than 300 ms). It stands still when the OS asks for reduced motion.
import { useEffect, useRef, type ReactNode } from 'react';
import { AccessibilityInfo, Animated, View } from 'react-native';
import { skeletons, useSkeletonGate, type SkeletonName, type SkeletonNode } from '@handline/app-data';
import { usePalette } from './theme.ts';

function usePulse(on: boolean): Animated.Value {
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (!on) return;
    let loop: Animated.CompositeAnimation | undefined;
    let gone = false;
    void AccessibilityInfo.isReduceMotionEnabled().then((reduced) => {
      if (gone || reduced) return;
      loop = Animated.loop(
        Animated.sequence([
          Animated.timing(opacity, { toValue: 0.5, duration: 700, useNativeDriver: true }),
          Animated.timing(opacity, { toValue: 1, duration: 700, useNativeDriver: true }),
        ]),
      );
      loop.start();
    });
    return () => {
      gone = true;
      loop?.stop();
    };
  }, [on, opacity]);
  return opacity;
}

function draw(node: SkeletonNode, color: string, opacity: Animated.Value, key: number): ReactNode {
  if ('box' in node)
    return (
      <Animated.View
        key={key}
        style={{ width: node.box.w as number, height: node.box.h, borderRadius: node.box.r ?? 0, backgroundColor: color, opacity }}
      />
    );
  if ('repeat' in node) return Array.from({ length: node.repeat }, (_, i) => draw(node.of, color, opacity, i));
  const kids = ('row' in node ? node.row : node.col).map((n, i) => draw(n, color, opacity, i));
  return (
    <View key={key} style={{ flexDirection: 'row' in node ? 'row' : 'column', gap: node.gap ?? 0, ...(node.h ? { height: node.h } : {}) }}>
      {kids}
    </View>
  );
}

/** A region's first-load shape, by name. `loading` is the region's own flag; nothing is drawn when it answers in time. */
export function Skeleton(props: { shape: SkeletonName; loading: boolean; label: string }) {
  const c = usePalette();
  const show = useSkeletonGate(props.loading);
  const opacity = usePulse(show);
  if (!show) return null;
  return (
    <View
      testID={`skeleton-${props.shape}`}
      accessibilityState={{ busy: true }}
      accessibilityLabel={`Loading ${props.label}`}
      style={{ padding: 16 }}
    >
      {draw(skeletons[props.shape], c.line, opacity, 0)}
    </View>
  );
}
