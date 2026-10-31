// The top of a page: where I am (title), the way back (parent's name), and the page's actions. The bell of the top-level tabs rides on the title row.
import { createContext, useContext } from 'react';
import { Pressable, Text, View } from 'react-native';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon } from '#src/screens/Icon.tsx';

export function BackLink(props: { label: string; onPress: () => void; testID?: string; accessibilityLabel?: string }) {
  const c = usePalette();
  return (
    <Pressable
      testID={props.testID}
      onPress={props.onPress}
      accessibilityRole="button"
      accessibilityLabel={props.accessibilityLabel ?? `Back to ${props.label}`}
      hitSlop={12}
      style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs, minHeight: 44 }}
    >
      <Icon name="back" decorative size={20} color={c.accent} />
      <Text style={{ color: c.accent, fontSize: font.size.lg }}>{props.label}</Text>
    </Pressable>
  );
}

export const BellSlot = createContext<React.ReactNode>(null);

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
  const bell = useContext(BellSlot);
  return (
    <View style={{ padding: props.bare ? 0 : space.lg, gap: space.sm }}>
      {back ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <BackLink label={back.label} onPress={back.onPress} testID={back.testID} />
          {props.title ? null : props.actions}
        </View>
      ) : null}
      {props.title || (props.actions && !back) ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <Text accessibilityRole="header" style={{ color: c.ink, fontSize: font.size.xl, fontWeight: '700', flex: 1 }}>
            {props.title}
          </Text>
          {props.actions}
          {back ? null : bell}
        </View>
      ) : null}
      {props.children}
    </View>
  );
}
