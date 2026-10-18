import { useEffect, useRef, useState } from 'react';
import { Keyboard, Platform, Pressable, Text, View, type ViewStyle } from 'react-native';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

export function Banner(props: { text: string; error?: boolean; onPress?: () => void }) {
  const c = usePalette();
  return (
    <Pressable
      onPress={props.onPress}
      accessibilityRole={props.error ? 'alert' : undefined}
      style={{ backgroundColor: props.error ? c.block : c.wait, padding: space.md }}
    >
      <Text style={{ color: '#fff' }}>
        {props.text}
        {props.error ? '  ✕' : ''}
      </Text>
    </Pressable>
  );
}

export function Button(props: {
  label: string;
  onPress: () => void;
  kind?: 'yes' | 'no' | 'plain';
  disabled?: boolean;
  style?: ViewStyle;
  testID?: string;
  accessibilityLabel?: string;
}) {
  const c = usePalette();
  const bg = props.kind === 'yes' ? c.run : props.kind === 'no' ? c.block : c.panel;
  const ink = props.kind === 'yes' || props.kind === 'no' ? '#fff' : c.ink;
  return (
    <Pressable
      testID={props.testID}
      disabled={props.disabled}
      onPress={props.onPress}
      accessibilityRole="button"
      accessibilityLabel={props.accessibilityLabel}
      style={{
        backgroundColor: bg,
        borderColor: c.line,
        borderWidth: 1,
        borderRadius: radius.md,
        paddingVertical: space.md,
        paddingHorizontal: space.lg,
        opacity: props.disabled ? 0.4 : 1,
        alignItems: 'center',
        ...props.style,
      }}
    >
      <Text style={{ color: ink, fontWeight: '600' }}>{props.label}</Text>
    </Pressable>
  );
}

/** The top of a page: where I am (title), the way back (parent's name), and the page's actions. */
export function PageHeader(props: {
  title?: string;
  back?: { label: string; onPress: () => void; testID?: string };
  actions?: React.ReactNode;
  children?: React.ReactNode;
  /** Inside a container that already pads. */
  bare?: boolean;
}) {
  const c = usePalette();
  const { back } = props;
  return (
    <View style={{ padding: props.bare ? 0 : space.lg, gap: space.sm }}>
      {back ? (
        <Pressable
          testID={back.testID}
          onPress={back.onPress}
          accessibilityRole="button"
          accessibilityLabel={`Back to ${back.label}`}
          hitSlop={12}
          style={{ alignSelf: 'flex-start' }}
        >
          <Text style={{ color: c.accent, fontSize: 16 }}>‹ {back.label}</Text>
        </Pressable>
      ) : null}
      {props.title || props.actions ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <Text accessibilityRole="header" style={{ color: c.ink, fontSize: 22, fontWeight: '700', flex: 1 }}>
            {props.title}
          </Text>
          {props.actions}
        </View>
      ) : null}
      {props.children}
    </View>
  );
}

/** The boxed panel a form or a confirmation sits in. */
export function Card(props: { children: React.ReactNode; testID?: string }) {
  const c = usePalette();
  return (
    <View
      testID={props.testID}
      style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, backgroundColor: c.panel }}
    >
      {props.children}
    </View>
  );
}

/** A rounded choice: the picked one is ringed and ticked. `role` is radio for one pick among several, button for a toggle. */
export function Pill(props: { on: boolean; label: string; testID: string; onPress: () => void; role?: 'button' | 'radio' }) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole={props.role ?? 'button'}
      accessibilityState={{ selected: props.on }}
      onPress={props.onPress}
      style={{
        minHeight: 36,
        justifyContent: 'center',
        borderWidth: props.on ? 2 : 1,
        borderColor: props.on ? c.ink : c.line,
        borderRadius: radius.pill,
        paddingHorizontal: space.md,
      }}
    >
      <Text style={{ color: c.ink, fontWeight: props.on ? '700' : '400' }}>{props.on ? `✓ ${props.label}` : props.label}</Text>
    </Pressable>
  );
}

/** A list row of a title over one muted line, set off by a rule above it. `style` adds to the row (a mark on its edge, dimming). */
export function ListRow(props: { title: string; meta: string; metaColor?: string; onPress: () => void; testID: string; style?: ViewStyle }) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      onPress={props.onPress}
      style={{
        minHeight: 48,
        justifyContent: 'center',
        paddingHorizontal: space.lg,
        paddingVertical: space.sm,
        borderTopWidth: 1,
        borderTopColor: c.line,
        ...props.style,
      }}
    >
      <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '600' }}>
        {props.title}
      </Text>
      <Text style={{ color: props.metaColor ?? c.muted, fontSize: 12 }}>{props.meta}</Text>
    </Pressable>
  );
}

export const Row = (props: { children: React.ReactNode; style?: ViewStyle }) => (
  <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, ...props.style }}>{props.children}</View>
);

/** A column that stays above the keyboard: its bottom padding is how far the keyboard reaches into it. */
export function AboveKeyboard(props: { children: React.ReactNode }) {
  const ref = useRef<View>(null);
  const [pad, setPad] = useState(0);
  useEffect(() => {
    // Android has no "will" events.
    const ios = Platform.OS === 'ios';
    const sub = Keyboard.addListener(ios ? 'keyboardWillChangeFrame' : 'keyboardDidShow', (e) => {
      const top = e.endCoordinates.screenY;
      ref.current?.measureInWindow((_x, y, _w, h) => setPad(Math.max(0, y + h - top)));
    });
    const hide = Keyboard.addListener(ios ? 'keyboardWillHide' : 'keyboardDidHide', () => setPad(0));
    return () => {
      sub.remove();
      hide.remove();
    };
  }, []);
  return (
    <View ref={ref} style={{ flex: 1, paddingBottom: pad }}>
      {props.children}
    </View>
  );
}

/** A small text link. */
export function Link(props: { label: string; onPress: () => void; testID: string; disabled?: boolean }) {
  const c = usePalette();
  return (
    <Pressable testID={props.testID} accessibilityRole="button" disabled={props.disabled} hitSlop={10} onPress={props.onPress} style={{ opacity: props.disabled ? 0.4 : 1 }}>
      <Text style={{ color: c.accent, fontSize: 12 }}>{props.label}</Text>
    </Pressable>
  );
}
