// A review card on the Inbox: the pictures of the task's first comment (tap = full screen) and two large buttons. Approve closes the task;
// Ask for change takes the words and, if wanted, marks on the first picture (MarkPicture), comments them and closes the task.
import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { SaveFormat, manipulateAsync } from 'expo-image-manipulator';
import { APPROVED, changeAsked, keptMarks, markText, reviewPictures, withImages, type Mark } from '@handline/app-core';
import { classify, sentence, useUploadImage, useMemos, useWrite } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Pictures, useImage } from './ChatPictures.tsx';
import { MarkPicture, type Marked } from './MarkPicture.tsx';
import { Button } from './parts.tsx';

const BIG = { minHeight: 52, flex: 1 } as const;

/** The pictures to look at; empty while the comments load or when the first comment has none. */
export function useReviewPictures(task: number) {
  return reviewPictures(useMemos(task).data?.memos ?? []);
}

export function ReviewCard(props: { task: number; item: number; off: string | null; markRead: () => void }) {
  const c = usePalette();
  const pictures = useReviewPictures(props.task);
  const first = useImage(pictures[0]?.id ?? '', false);
  const done = useWrite('task.done');
  const comment = useWrite('task.comment.add', String(props.task));
  const upload = useUploadImage();
  const [asking, setAsking] = useState(false);
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
  const finish = (evidence: string) =>
    done.mutateAsync({ task: props.task, evidence, reason: true }).then(() => props.markRead());
  const send = async () => {
    setSending(true);
    setProblem('');
    try {
      const drawn = kept.length && marked?.blob.size ? await upload(marked.blob, 'drawing.jpg', 'image/jpeg') : null;
      const marks: Mark[] = marked?.marks ?? [];
      await comment.mutateAsync({ task: props.task, text: withImages(markText(note, marks), drawn ? [drawn.id] : [], 'drawing') });
      await finish(changeAsked(note));
    } catch (e) {
      setProblem(sentence(classify(e)));
      setSending(false);
    }
  };
  return (
    <View testID="review-card" style={{ gap: space.md }}>
      {pictures.length > 0 && <Pictures items={pictures} />}
      {asking ? (
        <View style={{ gap: space.sm }}>
          <TextInput
            testID="review-note"
            autoFocus
            multiline
            value={note}
            onChangeText={setNote}
            placeholder="What should change?"
            placeholderTextColor={c.muted}
            accessibilityLabel="What should change"
            style={{ color: c.ink, borderWidth: 1, borderColor: c.line, borderRadius: radius.md, padding: space.md, minHeight: 64 }}
          />
          {problem ? (
            <Text accessibilityRole="alert" style={{ color: c.block }}>
              {problem}
            </Text>
          ) : null}
          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Button
              testID="review-mark"
              label={kept.length ? `Marked (${kept.length})` : 'Mark the picture'}
              disabled={first.state !== 'ready' || sending}
              onPress={() => void mark()}
              style={BIG}
            />
            <Button testID="review-send" kind="yes" label="Send" disabled={sending || !note.trim()} onPress={() => void send()} style={BIG} />
          </View>
          <Button label="Cancel" disabled={sending} onPress={() => setAsking(false)} />
          {shot && <MarkPicture uri={shot} start={marked?.marks} onCancel={() => setShot(null)} onDone={(r) => (setMarked(r ?? marked), setShot(null))} />}
        </View>
      ) : (
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Button
            testID={`inbox-approve-${props.item}`}
            kind="yes"
            label="Approve"
            disabled={!!props.off || done.isPending}
            onPress={() => void finish(APPROVED).catch(() => {})} // the refusal is reported by the write itself
            style={BIG}
          />
          <Button testID={`inbox-ask-${props.item}`} label="Ask for change" disabled={!!props.off} onPress={() => setAsking(true)} style={BIG} />
        </View>
      )}
    </View>
  );
}
