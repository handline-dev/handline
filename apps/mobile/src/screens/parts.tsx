import { useEffect, useRef, useState } from 'react';
import { Keyboard, KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, Text, View, type ViewStyle } from 'react-native';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';

/** A full-screen region that keeps clear of the notch, the status bar and the navigation bar. It carries its own provider because a Modal is a separate window whose insets the app's provider does not see. */
export function SafeScreen(props: { children: React.ReactNode; background: string; testID?: string }) {
  return (
    <SafeAreaProvider>
      <Padded {...props} />
    </SafeAreaProvider>
  );
}

function Padded(props: { children: React.ReactNode; background: string; testID?: string }) {
  const { top, bottom, left, right } = useSafeAreaInsets();
  return (
    <View
      testID={props.testID}
      style={{ flex: 1, backgroundColor: props.background, paddingTop: top, paddingBottom: bottom, paddingLeft: left, paddingRight: right }}
    >
      {props.children}
    </View>
  );
}

/** Insets of the window a Modal opens: read inside a SafeAreaProvider that the Modal itself holds. */
export function InsetModal(props: { children: React.ReactNode }) {
  return <SafeAreaProvider style={{ flex: 1 }}>{props.children}</SafeAreaProvider>;
}

/** An empty region: an icon over one line, so it never reads as a blank screen. The PC's ui/Empty is the same shape. */
export function EmptyState(props: { text: string; icon?: IconName; testID?: string }) {
  const c = usePalette();
  return (
    <View style={{ alignItems: 'center', gap: space.sm, padding: space.lg, paddingTop: space.lg * 2 }}>
      <Icon name={props.icon ?? 'inbox'} color={c.muted} size={24} decorative />
      <Text testID={props.testID ?? 'empty'} style={{ color: c.muted, textAlign: 'center' }}>
        {props.text}
      </Text>
    </View>
  );
}

export function Banner(props: { text: string; error?: boolean; action?: string; onPress?: () => void }) {
  const c = usePalette();
  return (
    <Pressable
      onPress={props.onPress}
      accessibilityRole={props.error ? 'alert' : undefined}
      style={{ backgroundColor: props.error ? c.block : c.wait, padding: space.md }}
    >
      <Text style={{ color: c.onFill }}>
        {props.text}
        {props.error ? '  ✕' : ''}
        {props.action ? `   ${props.action}` : ''}
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
  /** Drawn before the label; with an empty label it is the whole button. */
  icon?: IconName;
}) {
  const c = usePalette();
  const bg = props.kind === 'yes' ? c.run : props.kind === 'no' ? c.block : c.panel;
  const ink = props.kind === 'yes' || props.kind === 'no' ? c.onFill : c.ink;
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
        borderRadius: radius.card,
        paddingVertical: space.md,
        paddingHorizontal: space.lg,
        opacity: props.disabled ? 0.4 : 1,
        alignItems: 'center',
        flexDirection: 'row',
        justifyContent: 'center',
        gap: space.sm,
        ...props.style,
      }}
    >
      {props.icon ? <Icon name={props.icon} decorative size={18} color={ink} /> : null}
      {props.label ? <Text style={{ color: ink, fontWeight: '600' }}>{props.label}</Text> : null}
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
          <Text style={{ color: c.accent, fontSize: font.size.title }}>‹ {back.label}</Text>
        </Pressable>
      ) : null}
      {props.title || props.actions ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <Text accessibilityRole="header" style={{ color: c.ink, fontSize: font.size.page, fontWeight: '700', flex: 1 }}>
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
      style={{ gap: space.sm, padding: space.md, borderWidth: 1, borderColor: c.line, borderRadius: radius.card, backgroundColor: c.panel }}
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
export function ListRow(props: {
  title: string;
  meta: string;
  metaColor?: string;
  onPress: () => void;
  onLongPress?: () => void;
  selected?: boolean;
  testID: string;
  style?: ViewStyle;
  lead?: React.ReactNode;
  trail?: React.ReactNode;
}) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      onPress={props.onPress}
      onLongPress={props.onLongPress}
      accessibilityState={props.selected === undefined ? undefined : { selected: props.selected }}
      style={{
        minHeight: 48,
        backgroundColor: props.selected ? c.panel : undefined,
        justifyContent: 'center',
        paddingHorizontal: space.lg,
        paddingVertical: space.sm,
        borderTopWidth: 1,
        borderTopColor: c.line,
        flexDirection: 'row',
        alignItems: 'center',
        gap: space.md,
        ...props.style,
      }}
    >
      {props.lead}
      <View style={{ flex: 1 }}>
        <Text numberOfLines={1} style={{ color: c.ink, fontWeight: '600' }}>
          {props.title}
        </Text>
        <Text style={{ color: props.metaColor ?? c.muted, fontSize: font.size.label }}>{props.meta}</Text>
      </View>
      {props.trail}
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
    <Pressable
      testID={props.testID}
      accessibilityRole="button"
      disabled={props.disabled}
      hitSlop={10}
      onPress={props.onPress}
      style={{ opacity: props.disabled ? 0.4 : 1 }}
    >
      <Text style={{ color: c.accent, fontSize: font.size.label }}>{props.label}</Text>
    </Pressable>
  );
}

/** A sheet at the bottom of the screen over a dimmed page that a tap on closes; it rides above the keyboard. */
export function BottomSheet(props: { onClose: () => void; children: React.ReactNode; testID?: string; padded?: boolean }) {
  return (
    <Modal transparent visible animationType="fade" onRequestClose={props.onClose}>
      <InsetModal>
        <SheetBody {...props} />
      </InsetModal>
    </Modal>
  );
}

function SheetBody(props: { onClose: () => void; children: React.ReactNode; testID?: string; padded?: boolean }) {
  const c = usePalette();
  const { bottom } = useSafeAreaInsets();
  return (
    <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
      <Pressable
        accessibilityLabel="Close the sheet"
        onPress={props.onClose}
        style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: 'rgba(0,0,0,0.35)' }}
      >
        <View
          testID={props.testID}
          onStartShouldSetResponder={() => true}
          style={{
            backgroundColor: c.bg,
            borderTopLeftRadius: radius.dialog,
            borderTopRightRadius: radius.dialog,
            paddingBottom: space.xl + bottom,
            maxHeight: '85%',
          }}
        >
          <ScrollView
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={props.padded ? { padding: space.lg, gap: space.sm } : undefined}
          >
            {props.children}
          </ScrollView>
        </View>
      </Pressable>
    </KeyboardAvoidingView>
  );
}

/** A phone menu: rows in a bottom sheet. */
export function MenuSheet(props: { onClose: () => void; children: React.ReactNode; testID?: string }) {
  return <BottomSheet {...props} />;
}

/** One row of a MenuSheet; a reason it cannot be used sits under the label. */
export function MenuItem(props: {
  label: string;
  onPress: () => void;
  testID?: string;
  danger?: boolean;
  disabled?: boolean;
  note?: string | null;
  icon?: IconName;
  /** the view showing now */ on?: boolean;
}) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole="button"
      accessibilityState={{ selected: props.on }}
      disabled={props.disabled}
      onPress={props.onPress}
      style={{
        minHeight: 52,
        flexDirection: 'row',
        alignItems: 'center',
        gap: space.md,
        paddingHorizontal: space.lg,
        borderBottomWidth: 1,
        borderBottomColor: c.line,
        opacity: props.disabled ? 0.4 : 1,
      }}
    >
      {props.icon ? <Icon name={props.icon} decorative size={20} color={props.danger ? c.block : props.on ? c.accent : c.muted} /> : null}
      <View style={{ flex: 1 }}>
        <Text
          style={{
            color: props.danger ? c.block : props.on ? c.accent : c.ink,
            fontSize: font.size.title,
            fontWeight: props.on ? '700' : '400',
          }}
        >
          {props.label}
        </Text>
        {props.note ? <Text style={{ color: c.muted, fontSize: font.size.label }}>{props.note}</Text> : null}
      </View>
    </Pressable>
  );
}
