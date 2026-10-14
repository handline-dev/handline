// A text that can name images (a task body, a memo): the picker uploads each picture and, once it is on the daemon, puts its Markdown line at
// the cursor. The chip stays only while it uploads or after it failed.
import { useEffect, useRef, useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { imageRef, insertImageRef } from '@handline/app-core';
import { MAX_PER_MESSAGE, useImageDrafts } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { pickImages } from '../ImagePick.ts';
import { usePalette } from '../theme.ts';
import { AttachButton, ImageChips } from './ImageDrafts.tsx';

export function ImageBodyInput(props: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  testID: string;
  placeholder?: string;
  editable?: boolean;
  /** Shown above the box (the field's name), as the other inputs of a form have. */
  titled?: boolean;
}) {
  const c = usePalette();
  const drafts = useImageDrafts();
  const cursor = useRef<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const value = useRef(props.value);
  value.current = props.value;
  // A picture that reached the daemon becomes its line in the text and its chip goes.
  useEffect(() => {
    for (const d of drafts.drafts) {
      if (!d.attachment) continue;
      const next = insertImageRef(value.current, cursor.current, imageRef(d.attachment.id, d.name.replace(/\.[^.]+$/, '')));
      value.current = next;
      cursor.current = null;
      props.onChange(next);
      drafts.remove(d.key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drafts.drafts]);
  const pick = () =>
    pickImages(MAX_PER_MESSAGE - drafts.drafts.length).then(drafts.add, (e) =>
      setError(e instanceof Error ? e.message : 'The photo library did not open'),
    );
  return (
    <View style={{ gap: space.xs }}>
      {props.titled !== false && <Text style={{ color: c.muted, fontWeight: '600' }}>{props.label}</Text>}
      <TextInput
        testID={props.testID}
        accessibilityLabel={props.label}
        placeholder={props.placeholder ?? props.label}
        placeholderTextColor={c.muted}
        value={props.value}
        onChangeText={props.onChange}
        onSelectionChange={(e) => void (cursor.current = e.nativeEvent.selection.end)}
        editable={props.editable}
        multiline
        style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 56 }}
      />
      <ImageChips drafts={drafts.drafts} problem={error ?? drafts.problem} onRetry={drafts.retry} onRemove={drafts.remove} />
      <AttachButton onPress={() => void pick()} disabled={props.editable === false} />
    </View>
  );
}
