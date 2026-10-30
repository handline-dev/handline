// The pictures of a sent message: thumbnails in the bubble, and the photo viewer behind a tap. The bytes are read with attachment.get
// like the web does and drawn from a data URI (React Native has no object URLs).
import { useMemo, useState } from 'react';
import { Image, Pressable, Text, View } from 'react-native';
import type { Attachment } from '@handline/client';
import { useAttachmentBytes } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { PhotoViewer, type SendMarks } from '#src/screens/PhotoViewer.tsx';

/** What a picture needs of its file: a chat attachment or an image a memo carries. */
export type Picture = Pick<Attachment, 'id' | 'name'>;

const toBase64 = (bytes: Uint8Array) => {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};

type Read = { state: 'loading' } | { state: 'ready'; uri: string } | { state: 'gone' } | { state: 'failed' };

// The bytes are not cached: each mount reads again (the web keeps one read per image).
export function useImage(id: string, thumb: boolean): Read {
  const read = useAttachmentBytes(id, thumb);
  return useMemo((): Read => {
    if (read.state !== 'ready') return read;
    try {
      return { state: 'ready', uri: `data:${read.mime};base64,${toBase64(read.bytes)}` };
    } catch {
      return { state: 'failed' }; // an original too large to encode
    }
  }, [read]);
}

const GONE = 'This image is no longer kept.';
const FAILED = 'This image could not be loaded.';

function Thumb({ a, onOpen }: { a: Picture; onOpen: () => void }) {
  const c = usePalette();
  const read = useImage(a.id, true);
  const [bad, setBad] = useState(false);
  const line = read.state === 'gone' ? GONE : read.state === 'failed' || bad ? FAILED : null;
  if (line)
    return (
      <Text testID={`picture-${a.id}`} accessibilityRole="alert" style={{ color: c.muted, fontSize: font.size.sm }}>
        {a.name}: {line}
      </Text>
    );
  return (
    <>
      <Pressable testID={`picture-${a.id}`} accessibilityRole="button" accessibilityLabel={`Open ${a.name}`} onPress={onOpen}>
        {read.state === 'ready' ? (
          <Image
            accessibilityLabel={a.name}
            source={{ uri: read.uri }}
            resizeMode="cover"
            onError={() => setBad(true)}
            style={{ width: 120, height: 120, borderRadius: radius.m }}
          />
        ) : (
          <Text style={{ color: c.muted, fontSize: font.size.sm }}>{a.name}</Text>
        )}
      </Pressable>
    </>
  );
}

/** The pictures of a message, in the order they were attached; a tap opens the viewer on that one. `onSend` lets the viewer send marked notes back to `to`. */
export function Pictures({ items, to, onSend }: { items: Picture[]; to?: string; onSend?: SendMarks }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <View testID="pictures" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xs }}>
      {items.map((a, i) => (
        <Thumb key={a.id} a={a} onOpen={() => setOpen(i)} />
      ))}
      {open !== null && <PhotoViewer pictures={items} start={open} to={to} onSend={onSend} onClose={() => setOpen(null)} />}
    </View>
  );
}
