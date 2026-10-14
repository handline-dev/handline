// The pictures of a sent message: thumbnails in the bubble, and one full screen behind a tap. The bytes are read with attachment.get
// like the web does and drawn from a data URI (React Native has no object URLs).
import { useEffect, useState } from 'react';
import { Image, Modal, Pressable, Text, View } from 'react-native';
import type { Attachment } from '@handline/client';
import { RpcError } from '@handline/client';
import { readImage } from '@handline/app-data';
import { useStore } from '@handline/app-react';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

type Read = { state: 'loading' } | { state: 'ready'; uri: string } | { state: 'gone' } | { state: 'failed' };

const toBase64 = (bytes: Uint8Array) => {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};

function useImage(id: string, thumb: boolean): Read {
  const store = useStore();
  const [read, setRead] = useState<Read>({ state: 'loading' });
  useEffect(() => {
    let live = true;
    setRead({ state: 'loading' });
    readImage((m, p) => store.client.call(m as never, p as never), id, thumb).then(
      ({ mime, bytes }) => live && setRead({ state: 'ready', uri: `data:${mime};base64,${toBase64(bytes)}` }),
      (e) => live && setRead({ state: e instanceof RpcError && e.code === 'not_found' ? 'gone' : 'failed' }),
    );
    return () => {
      live = false;
    };
  }, [store, id, thumb]);
  return read;
}

const GONE = 'This image is no longer kept.';
const FAILED = 'This image could not be loaded.';

function Full({ a, onClose }: { a: Attachment; onClose: () => void }) {
  const c = usePalette();
  const read = useImage(a.id, false);
  const [bad, setBad] = useState(false);
  const line = read.state === 'gone' ? GONE : read.state === 'failed' || bad ? FAILED : null;
  return (
    <Modal visible transparent animationType="fade" onRequestClose={onClose}>
      <View testID={`picture-full-${a.id}`} style={{ flex: 1, backgroundColor: c.bg, padding: space.md, gap: space.md }}>
        <Pressable testID="picture-close" accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} hitSlop={8}>
          <Text style={{ color: c.accent, fontWeight: '600' }}>Close</Text>
        </Pressable>
        {line ? (
          <Text testID="picture-line" accessibilityRole="alert" style={{ color: c.block }}>
            {line}
          </Text>
        ) : read.state === 'ready' ? (
          <Image
            testID="picture-image"
            accessibilityLabel={a.name}
            source={{ uri: read.uri }}
            resizeMode="contain"
            onError={() => setBad(true)}
            style={{ flex: 1 }}
          />
        ) : (
          <Text style={{ color: c.muted }}>Loading…</Text>
        )}
      </View>
    </Modal>
  );
}

function Thumb({ a }: { a: Attachment }) {
  const c = usePalette();
  const read = useImage(a.id, true);
  const [open, setOpen] = useState(false);
  const [bad, setBad] = useState(false);
  const line = read.state === 'gone' ? GONE : read.state === 'failed' || bad ? FAILED : null;
  if (line)
    return (
      <Text testID={`picture-${a.id}`} accessibilityRole="alert" style={{ color: c.muted, fontSize: 12 }}>
        {a.name}: {line}
      </Text>
    );
  return (
    <>
      <Pressable testID={`picture-${a.id}`} accessibilityRole="button" accessibilityLabel={`Open ${a.name}`} onPress={() => setOpen(true)}>
        {read.state === 'ready' ? (
          <Image
            accessibilityLabel={a.name}
            source={{ uri: read.uri }}
            resizeMode="cover"
            onError={() => setBad(true)}
            style={{ width: 120, height: 120, borderRadius: radius.md }}
          />
        ) : (
          <Text style={{ color: c.muted, fontSize: 12 }}>{a.name}</Text>
        )}
      </Pressable>
      {open && <Full a={a} onClose={() => setOpen(false)} />}
    </>
  );
}

/** The pictures of a message, in the order they were attached. */
export function Pictures({ items }: { items: Attachment[] }) {
  return (
    <View testID="pictures" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xs }}>
      {items.map((a) => (
        <Thumb key={a.id} a={a} />
      ))}
    </View>
  );
}
