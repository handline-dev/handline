import { useEffect, useRef, useState } from 'react';
import { Keyboard, Pressable, Text, View, type ViewStyle } from 'react-native';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

export function Banner(props: { text: string; error?: boolean; onPress?: () => void }) {
  const c = usePalette();
  return (
    <Pressable onPress={props.onPress} accessibilityRole={props.error ? 'alert' : undefined}
      style={{ backgroundColor: props.error ? c.block : c.wait, padding: space.md }}>
      <Text style={{ color: '#fff' }}>{props.text}{props.error ? '  ✕' : ''}</Text>
    </Pressable>
  );
}

export function Button(props: { label: string; onPress: () => void; kind?: 'yes' | 'no' | 'plain'; disabled?: boolean; style?: ViewStyle; testID?: string }) {
  const c = usePalette();
  const bg = props.kind === 'yes' ? c.run : props.kind === 'no' ? c.block : c.panel;
  const ink = props.kind === 'yes' || props.kind === 'no' ? '#fff' : c.ink;
  return (
    <Pressable testID={props.testID} disabled={props.disabled} onPress={props.onPress} accessibilityRole="button"
      style={{ backgroundColor: bg, borderColor: c.line, borderWidth: 1, borderRadius: radius.md, paddingVertical: space.md, paddingHorizontal: space.lg, opacity: props.disabled ? 0.4 : 1, alignItems: 'center', ...props.style }}>
      <Text style={{ color: ink, fontWeight: '600' }}>{props.label}</Text>
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
    const sub = Keyboard.addListener('keyboardWillChangeFrame', (e) => {
      const top = e.endCoordinates.screenY;
      ref.current?.measureInWindow((_x, y, _w, h) => setPad(Math.max(0, y + h - top)));
    });
    const hide = Keyboard.addListener('keyboardWillHide', () => setPad(0));
    return () => { sub.remove(); hide.remove(); };
  }, []);
  return <View ref={ref} style={{ flex: 1, paddingBottom: pad }}>{props.children}</View>;
}
