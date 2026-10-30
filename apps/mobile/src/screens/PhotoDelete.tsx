// The photo viewer's delete question: how many places the photo disappears from (a dry run counts them), then Delete or Keep. There is no undo.
import { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
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
    mutateAsync({ id: props.id, dry_run: true } as never).then(
      (r) => live && setCount((r as { removed_from: number }).removed_from),
      () => live && setProblem(true),
    );
    return () => {
      live = false;
    };
  }, [mutateAsync, props.id]);
  const go = () => {
    setProblem(false);
    mutateAsync({ id: props.id } as never).then(props.onDeleted, () => setProblem(true));
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
        <Text testID="delete-places" style={{ color: c.muted }}>
          {count === null ? 'Counting where it is used…' : `It disappears from ${places(count)}. This cannot be undone.`}
        </Text>
        {problem && (
          <Text testID="delete-problem" accessibilityRole="alert" style={{ color: c.block }}>
            The photo was not deleted. Try again.
          </Text>
        )}
        <Button testID="delete-keep" kind="yes" label="Keep it" onPress={props.onKeep} />
        <Pressable
          testID="delete-yes"
          accessibilityRole="button"
          hitSlop={8}
          disabled={count === null || remove.isPending}
          onPress={go}
          style={{ alignItems: 'center', padding: space.sm, opacity: count === null ? 0.4 : 1 }}
        >
          <Text style={{ color: c.block, fontWeight: '600' }}>Delete</Text>
        </Pressable>
      </View>
    </View>
  );
}
