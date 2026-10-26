// The images of the message being written: picked from the photo library, uploaded at once, named in the send once all have arrived. A picture
// that arrived can be marked up (MarkPicture): the marked copy is drawn once, uploaded with the send and goes beside the plain one.
import { useState } from 'react';
import { View } from 'react-native';
import { space } from '@handline/tokens';
import { markText, type Mark } from '@handline/app-core';
import { MAX_PER_MESSAGE, useImageDrafts, useUploadImage } from '@handline/app-data';
import { pickImages } from '#src/ImagePick.ts';
import { AttachButton, ImageChips } from '#src/screens/ImageDrafts.tsx';
import { MarkPicture, type Marked } from '#src/screens/MarkPicture.tsx';

interface Sent {
  text: string;
  attachments: string[];
}

export function useChatImages(canSend: boolean, agent: string) {
  const d = useImageDrafts(MAX_PER_MESSAGE, agent); // kept per agent: still there when the person comes back to this chat
  const upload = useUploadImage();
  const [error, setError] = useState<string | null>(null);
  const [marking, setMarking] = useState<number | null>(null);
  const [marked, setMarked] = useState<(Marked & { key: number }) | null>(null);
  const pick = () => {
    setError(null);
    pickImages(MAX_PER_MESSAGE - d.drafts.length).then(d.add, (e) =>
      setError(e instanceof Error ? e.message : 'The photo library did not open'),
    );
  };
  const draft = d.drafts.find((x) => x.key === marking);
  const keep = (marks: Mark[]) => marks.some((m) => m.text.trim());
  const clear = () => {
    d.clear();
    setMarked(null);
    setMarking(null);
  };
  return {
    ids: d.ids,
    /** One is still uploading or failed: the message waits. */
    busy: d.busy,
    clear,
    /** The words and the pictures of the send: the marked copy goes up now, and the memos become numbered lines beside the note. */
    finish(note: string): Sent | Promise<Sent> {
      if (!marked || !keep(marked.marks)) return { text: note, attachments: d.ids };
      const { blob, marks } = marked;
      const text = markText(note, marks);
      if (!blob.size) return { text, attachments: d.ids };
      return upload(blob, 'marked.jpg', 'image/jpeg').then((drawn) => ({ text, attachments: [...d.ids, drawn.id] }));
    },
    chips: (
      <>
        <View style={{ paddingHorizontal: space.sm }}>
          <ImageChips
            drafts={d.drafts}
            problem={error ?? d.problem}
            onRetry={d.retry}
            onRemove={(k) => {
              if (marked?.key === k) setMarked(null);
              d.remove(k);
            }}
            onMark={setMarking}
            marked={marked && keep(marked.marks) ? marked.key : null}
          />
        </View>
        {draft && (
          <MarkPicture
            uri={draft.preview}
            start={marked?.key === draft.key ? marked.marks : undefined}
            onCancel={() => setMarking(null)}
            onDone={(r) => {
              setMarked(r && keep(r.marks) ? { ...r, key: draft.key } : marked?.key === draft.key ? null : marked);
              setMarking(null);
            }}
          />
        )}
      </>
    ),
    button: <AttachButton onPress={pick} disabled={!canSend} />,
  };
}
