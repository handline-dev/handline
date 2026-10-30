// What a review item needs besides its words: the pictures of the task's first comment (tap = full screen), and Ask for change, which takes the
// words and, if wanted, marks on the first picture (MarkPicture), comments them and closes the task.
import { useState } from 'react';
import { TextInput } from 'react-native';
import { SaveFormat, manipulateAsync } from 'expo-image-manipulator';
import { changeAsked, keptMarks, markText, withImages, type Mark } from '@handline/app-core';
import { classify, sentence, useReviewPictures, useUploadImage, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Pictures, useImage } from '#src/screens/ChatPictures.tsx';
import { MarkPicture, type Marked } from '#src/screens/MarkPicture.tsx';
import { Button } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

export function ReviewPictures({ task }: { task: number }) {
  const pictures = useReviewPictures(task);
  return pictures.length > 0 ? <Pictures items={pictures} deletable /> : null;
}

/** The sheet that asks what should change; a sent one closes the task with the words and calls `onSent`. */
export function AskChange(props: { task: number; onClose: () => void; onSent: (words: string) => void }) {
  const c = usePalette();
  const pictures = useReviewPictures(props.task);
  const first = useImage(pictures[0]?.id ?? '', false);
  const done = useWrite('task.done');
  const comment = useWrite('task.comment.add', String(props.task));
  const upload = useUploadImage();
  const [shot, setShot] = useState<string | null>(null); // the first picture as a file: the marker measures a picture by its address, which a data address does not give on Android
  const [marked, setMarked] = useState<Marked | null>(null);
  const [note, setNote] = useState('');
  const [problem, setProblem] = useState('');
  const [sending, setSending] = useState(false);
  const kept = marked ? keptMarks(marked.marks) : [];
  const mark = () =>
    first.state === 'ready' &&
    manipulateAsync(first.uri, [], { format: SaveFormat.PNG }).then(
      (r) => setShot(r.uri),
      (e: unknown) => setProblem(sentence(classify(e))),
    );
  const send = async () => {
    setSending(true);
    setProblem('');
    try {
      const drawn = kept.length && marked?.blob.size ? await upload(marked.blob, 'drawing.jpg', 'image/jpeg') : null;
      const marks: Mark[] = marked?.marks ?? [];
      await comment.mutateAsync({ task: props.task, text: withImages(markText(note, marks), drawn ? [drawn.id] : [], 'drawing') });
      await done.mutateAsync({ task: props.task, evidence: changeAsked(note), reason: true });
      props.onSent(note);
    } catch (e) {
      setProblem(sentence(classify(e)));
      setSending(false);
    }
  };
  return (
    <Sheet
      id="review"
      label="What should change?"
      submit="Send"
      busy={sending || !note.trim()}
      onCancel={() => !sending && props.onClose()}
      onSubmit={() => void send()}
      error={problem || undefined}
    >
      <TextInput
        testID="review-note"
        autoFocus
        multiline
        value={note}
        onChangeText={setNote}
        placeholder="What should change?"
        placeholderTextColor={c.muted}
        accessibilityLabel="What should change"
        style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.m, padding: space.md, minHeight: 64 }}
      />
      <Button
        testID="review-mark"
        label={kept.length ? `Marked (${kept.length})` : 'Mark the picture'}
        disabled={first.state !== 'ready' || sending}
        onPress={() => void mark()}
      />
      {shot && (
        <MarkPicture
          uri={shot}
          start={marked?.marks}
          onCancel={() => setShot(null)}
          onDone={(r) => {
            setMarked(r ?? marked);
            setShot(null);
          }}
        />
      )}
    </Sheet>
  );
}
