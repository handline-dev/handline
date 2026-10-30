// The photo viewer's delete question: how many places the photo disappears from (a dry run counts them), then Delete or Keep. There is no undo.
import { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';

export const places = (n: number) => `${n} ${n === 1 ? 'place' : 'places'}`;

export function DeleteAsk(props: { id: string; onDeleted: () => void; onKeep: () => void }) {
  const c = usePalette();
  const remove = useWrite('attachment.remove');
  const [count, setCount] = useState<number | null>(null);
  const [problem, setProblem] = useState(false);
  const { mutateAsync } = remove;
  useEffect(() => {
    let live = true;
    mutateAsync({ id: props.id, dry_run: true }).then(
      (r) => live && setCount(r.removed_from),
      () => live && setProblem(true),
    );
    return () => {
      live = false;
    };
  }, [mutateAsync, props.id]);
  const go = () => {
    setProblem(false);
    mutateAsync({ id: props.id }).then(props.onDeleted, () => setProblem(true));
  };
  return (
    <View
      testID="delete-ask"
      style={{
        position: 'absolute',
        left: 0,
        right: 0,
        top: 0,
        bottom: 0,
        backgroundColor: 'rgba(0,0,0,0.6)',
        justifyContent: 'center',
        padding: space.xl,
      }}
    >
      <View style={{ backgroundColor: c.panel, borderRadius: radius.m, padding: space.xl, gap: space.md }}>
        <Text style={{ color: c.ink, fontSize: font.size.lg, fontWeight: '700' }}>Delete this photo?</Text>
        {count !== 0 && (
          <Text testID="delete-places" style={{ color: c.muted }}>
            {count === null ? 'Counting where it is used…' : `It disappears from ${places(count)}. This cannot be undone.`}
          </Text>
        )}
        {problem && (
          <Text testID="delete-problem" accessibilityRole="alert" style={{ color: c.block }}>
            The photo was not deleted. Try again.
          </Text>
        )}
        <View style={{ flexDirection: 'row', gap: space.md, paddingTop: space.sm }}>
          <Button testID="delete-keep" label="Cancel" onPress={props.onKeep} style={{ flex: 1 }} />
          <Button
            testID="delete-yes"
            kind="no"
            label="Delete"
            disabled={count === null || remove.isPending}
            onPress={go}
            style={{ flex: 1 }}
          />
        </View>
      </View>
    </View>
  );
}
