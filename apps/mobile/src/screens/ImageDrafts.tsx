// The images on their way with a text: a chip for each (its picture, the progress, Retry when the upload failed, a remove mark) and the button
// that opens the photo library.
import { Image, Pressable, Text, View } from 'react-native';
import { font, radius, space } from '@handline/tokens';
import type { ImageDraft } from '@handline/app-data';
import { usePalette } from '#src/theme.ts';
import { MARK_COLOR } from '@handline/app-core';

export function ImageChips(props: {
  drafts: ImageDraft[];
  problem: string | null;
  onRetry: (key: number) => void;
  onRemove: (key: number) => void;
  /** Tapping a chip whose picture arrived opens it to mark up; the one marked wears a dot. */
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
            borderRadius: radius.m,
            padding: 2,
          }}
        >
          <Pressable
            testID={`image-mark-${d.key}`}
            accessibilityRole="button"
            accessibilityLabel={`Open ${d.name} to mark it`}
            disabled={!props.onMark || !d.attachment}
            onPress={() => props.onMark?.(d.key)}
          >
            <Image source={{ uri: d.preview }} accessibilityLabel={d.name} style={{ width: 48, height: 48, borderRadius: radius.s }} />
            {props.marked === d.key ? (
              <View
                testID={`image-marked-${d.key}`}
                accessibilityLabel="Marked"
                style={{ position: 'absolute', right: -3, top: -3, width: 12, height: 12, borderRadius: 6, backgroundColor: MARK_COLOR, borderWidth: 1, borderColor: c.bg }}
              />
            ) : null}
          </Pressable>
          {d.error ? (
            <Pressable
              testID={`image-retry-${d.key}`}
              accessibilityRole="button"
              accessibilityLabel={`Retry ${d.name}`}
              hitSlop={8}
              onPress={() => props.onRetry(d.key)}
            >
              <Text style={{ color: c.block, fontSize: font.size.sm }}>Retry</Text>
            </Pressable>
          ) : !d.attachment ? (
            <Text testID={`image-progress-${d.key}`} style={{ color: c.muted, fontSize: font.size.sm }}>
              {Math.round(d.progress * 100)}%
            </Text>
          ) : null}
          <Pressable
            testID={`image-remove-${d.key}`}
            accessibilityRole="button"
            accessibilityLabel={`Remove ${d.name}`}
            hitSlop={10}
            onPress={() => props.onRemove(d.key)}
          >
            <Text style={{ color: c.muted, fontSize: font.size.md }}>✕</Text>
          </Pressable>
        </View>
      ))}
      {props.problem ? (
        <Text testID="image-problem" accessibilityRole="alert" style={{ color: c.block, fontSize: font.size.sm }}>
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
      <Text style={{ color: c.accent, fontSize: font.size.xl }}>＋</Text>
    </Pressable>
  );
}
