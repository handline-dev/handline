// The ... menu of the chat: a small popover under the top bar with the views (Terminal, Files, Tasks), then Find in chat and Start fresh now.
// Everything else the agent can be told to do stays behind one More row, which opens the full list.
import type { ReactNode } from 'react';
import { Modal, Pressable, Text, View } from 'react-native';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';

export function PopRow(props: { testID?: string; label: string; icon: IconName; onPress: () => void; disabled?: boolean }) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole="button"
      disabled={props.disabled}
      onPress={props.onPress}
      style={{ minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: space.md, paddingHorizontal: space.md, opacity: props.disabled ? 0.4 : 1 }}
    >
      <Icon name={props.icon} decorative size={18} color={c.ink} />
      <Text style={{ color: c.ink, fontSize: font.size.md }}>{props.label}</Text>
    </Pressable>
  );
}

export function PopDivider() {
  const c = usePalette();
  return <View style={{ height: 1, backgroundColor: c.line }} />;
}

function Pop(props: { onClose: () => void; children: ReactNode }) {
  const c = usePalette();
  const top = useSafeAreaInsets().top + 48;
  return (
    <Pressable testID="verbs-backdrop" accessibilityLabel="Close the menu" onPress={props.onClose} style={{ flex: 1 }}>
      <View
        testID="verbs"
        style={{
          position: 'absolute',
          top,
          right: space.md,
          minWidth: 220,
          paddingVertical: space.xs,
          borderRadius: radius.l,
          borderWidth: 1,
          borderColor: c.line,
          backgroundColor: c.bg,
          shadowColor: '#000',
          shadowOpacity: 0.15,
          shadowRadius: 12,
          shadowOffset: { width: 0, height: 4 },
          elevation: 6,
        }}
      >
        {props.children}
      </View>
    </Pressable>
  );
}

export function ChatMenu(props: { onClose: () => void; children: ReactNode }) {
  return (
    <Modal transparent visible animationType="fade" onRequestClose={props.onClose}>
      <SafeAreaProvider style={{ flex: 1 }}>
        <Pop onClose={props.onClose}>{props.children}</Pop>
      </SafeAreaProvider>
    </Modal>
  );
}
