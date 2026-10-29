// The files a memo carries (Memo.files): images as thumbnails that open full size, Markdown rendered under its name. The bytes come from attachment.get.
import { useMemo } from 'react';
import { Text, View } from 'react-native';
import type { MemoFile } from '@handline/client';
import { useAttachmentBytes } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Pictures } from '#src/screens/ChatPictures.tsx';
import { Markdown } from '#src/screens/Markdown.tsx';

function MarkdownFile({ file }: { file: MemoFile }) {
  const c = usePalette();
  const read = useAttachmentBytes(file.id, false);
  const text = useMemo(() => (read.state === 'ready' ? new TextDecoder().decode(read.bytes) : null), [read]);
  return (
    <View testID={`memo-file-${file.id}`} style={{ gap: space.xs }}>
      <Text style={{ color: c.muted, fontSize: font.size.sm }}>{file.name}</Text>
      {text !== null ? (
        <Markdown text={text} testID={`memo-markdown-${file.id}`} />
      ) : (
        <Text accessibilityRole={read.state === 'loading' ? undefined : 'alert'} style={{ color: c.muted }}>
          {read.state === 'loading' ? 'Loading…' : read.state === 'gone' ? 'This file is no longer kept.' : 'This file could not be loaded.'}
        </Text>
      )}
    </View>
  );
}

export function MemoFiles({ files }: { files: MemoFile[] }) {
  const c = usePalette();
  const kept = files.filter((f) => !f.gone);
  const images = kept.filter((f) => f.kind === 'image');
  return (
    <View testID="memo-files" style={{ gap: space.sm }}>
      {images.length > 0 && <Pictures items={images} />}
      {kept.filter((f) => f.kind === 'markdown').map((f) => (
        <MarkdownFile key={f.id} file={f} />
      ))}
      {files.filter((f) => f.gone).map((f) => (
        <Text key={f.id} style={{ color: c.muted, fontSize: font.size.sm }}>{`${f.name}: no longer kept.`}</Text>
      ))}
    </View>
  );
}
