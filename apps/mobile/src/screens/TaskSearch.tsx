// The search box above the task list.
import { TextInput, View } from 'react-native';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';

export function TaskSearch(props: { value: string; onChange: (v: string) => void }) {
  const c = usePalette();
  return (
    <View style={{ paddingHorizontal: space.lg, paddingBottom: space.sm }}>
      <TextInput
        testID="tasks-search"
        accessibilityLabel="Search tasks"
        placeholder="Search by number, title or agent"
        placeholderTextColor={c.muted}
        value={props.value}
        onChangeText={props.onChange}
        autoCapitalize="none"
        autoCorrect={false}
        clearButtonMode="while-editing"
        style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: 10, paddingHorizontal: space.md, minHeight: 40 }}
      />
    </View>
  );
}
