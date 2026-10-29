// The title of one group of the task list: its name and how many tasks it holds.
import { Text } from 'react-native';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

export function GroupHead(props: { title: string; count: number }) {
  const c = usePalette();
  return (
    <Text
      accessibilityRole="header"
      style={{
        color: c.muted,
        fontSize: font.size.sm,
        fontWeight: '700',
        paddingHorizontal: space.lg,
        paddingTop: space.lg,
        paddingBottom: space.xs,
        backgroundColor: c.bg,
      }}
    >
      {`${props.title} ${props.count}`}
    </Text>
  );
}
