// The images on their way with a text: a chip for each (its picture, the progress, Retry when the upload failed, a remove mark) and the button
// that opens the photo library.
import { Image, Pressable, Text, View } from 'react-native';
import { radius, space } from '@handline/tokens';
import type { ImageDraft } from '@handline/app-data';
import { usePalette } from '../theme.ts';
import { Link } from './parts.tsx';

export function ImageChips(props: {
  drafts: ImageDraft[];
  problem: string | null;
  onRetry: (key: number) => void;
  onRemove: (key: number) => void;
  /** A chip whose picture arrived can be marked up; the one marked says so. */
  onMark?: (key: number) => void;
  marked?: number | null;
}) {
  const c = usePalette();
  if (!props.drafts.length && !props.problem) return null;
  return (
    <View testID="image-chips" style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: space.xs }}>
      {props.drafts.map((d) => (
        <View
          key={d.key}
          testID={`image-chip-${d.key}`}
          accessibilityLabel={d.error ? `${d.name}: ${d.error}` : d.name}
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: space.xs,
            borderWidth: 1,
            borderColor: d.error ? c.block : c.line,
            borderRadius: radius.md,
            padding: 2,
          }}
        >
          <Image source={{ uri: d.preview }} accessibilityLabel={d.name} style={{ width: 36, height: 36, borderRadius: radius.sm }} />
          {d.error ? (
            <Pressable
              testID={`image-retry-${d.key}`}
              accessibilityRole="button"
              accessibilityLabel={`Retry ${d.name}`}
              hitSlop={8}
              onPress={() => props.onRetry(d.key)}
            >
              <Text style={{ color: c.block, fontSize: 12 }}>Retry</Text>
            </Pressable>
          ) : !d.attachment ? (
            <Text testID={`image-progress-${d.key}`} style={{ color: c.muted, fontSize: 12 }}>
              {Math.round(d.progress * 100)}%
            </Text>
          ) : props.onMark && d.attachment ? (
            <Link testID={`image-mark-${d.key}`} label={props.marked === d.key ? 'Marked' : 'Mark'} onPress={() => props.onMark!(d.key)} />
          ) : null}
          <Pressable
            testID={`image-remove-${d.key}`}
            accessibilityRole="button"
            accessibilityLabel={`Remove ${d.name}`}
            hitSlop={10}
            onPress={() => props.onRemove(d.key)}
          >
            <Text style={{ color: c.muted, fontSize: 14 }}>✕</Text>
          </Pressable>
        </View>
      ))}
      {props.problem ? (
        <Text testID="image-problem" accessibilityRole="alert" style={{ color: c.block, fontSize: 12 }}>
          {props.problem}
        </Text>
      ) : null}
    </View>
  );
}

export function AttachButton(props: { onPress: () => void; disabled?: boolean }) {
  const c = usePalette();
  return (
    <Pressable
      testID="attach-image"
      accessibilityRole="button"
      accessibilityLabel="Attach an image"
      disabled={props.disabled}
      hitSlop={8}
      onPress={props.onPress}
      style={{ padding: space.sm, opacity: props.disabled ? 0.4 : 1 }}
    >
      <Text style={{ color: c.accent, fontSize: 20 }}>＋</Text>
    </Pressable>
  );
}
