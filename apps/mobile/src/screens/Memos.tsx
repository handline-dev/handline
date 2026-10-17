// A task's memos on the phone (docs/pc-app-gaps.md U11): add, edit and delete, each a `useWrite` that shows at once and rolls back with the daemon's reason.
import { useState } from 'react';
import { Text, View } from 'react-native';
import type { MemoFile } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { ImageBodyInput } from './ImageBodyInput.tsx';
import { Button } from './parts.tsx';
import { MemoFiles } from './MemoFiles.tsx';
import { Input, Sheet, useWhyNot } from './TaskActions.tsx';
import { Markdown } from './Markdown.tsx';

/** One memo with Edit and Delete (as the web's MemoRow). Both show at once and roll back with the reason on a refusal; Delete asks first. */
export function MemoRow({ task, memo, when }: { task: number; memo: { id: number; author: string; text: string; files?: MemoFile[] }; when: string }) {
  const c = usePalette();
  const edit = useWrite('task.memo.edit', String(task));
  const del = useWrite('task.memo.delete', String(task));
  const offEdit = useWhyNot('task.memo.edit');
  const offDelete = useWhyNot('task.memo.delete');
  const [ask, setAsk] = useState<null | 'edit' | 'delete'>(null);
  const [text, setText] = useState('');
  const sending = memo.id < 0; // not yet numbered by the daemon
  const close = () => (setAsk(null), edit.reset(), del.reset());
  return (
    <View testID={`memo-${memo.id}`} style={{ gap: space.sm }}>
      <Text style={{ color: c.muted, fontSize: 12 }}>{`${memo.author} ${when}`}</Text>
      <Markdown text={memo.text} breaks />
      {memo.files?.length ? <MemoFiles files={memo.files} /> : null}
      {ask === null && (
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <Button
            testID={`memo-edit-${memo.id}`}
            label="Edit"
            disabled={sending || !!offEdit}
            onPress={() => (setText(memo.text), setAsk('edit'))}
          />
          <Button testID={`memo-delete-${memo.id}`} label="Delete" disabled={sending || !!offDelete} onPress={() => setAsk('delete')} />
        </View>
      )}
      {ask === null && (offEdit || offDelete) && <Text style={{ color: c.muted, fontSize: 12 }}>{offEdit || offDelete}</Text>}
      {ask === 'edit' && (
        <Sheet
          label="edit memo"
          submit="Save"
          busy={edit.isPending}
          onCancel={close}
          onSubmit={() => void (text.trim() && edit.mutateAsync({ task, memo: memo.id, text: text.trim() }).then(close, () => {}))}
          error={!text.trim() ? 'A memo needs text.' : edit.isError ? sentence(classify(edit.error)) : undefined}
        >
          <Input testID="memo-edit-text" label="Memo" value={text} onChange={setText} multiline />
        </Sheet>
      )}
      {ask === 'delete' && (
        <Sheet
          label="Delete this memo?"
          submit="Delete"
          kind="no"
          busy={del.isPending}
          onCancel={close}
          onSubmit={() => void del.mutateAsync({ task, memo: memo.id }).then(close, () => {})}
          error={del.isError ? sentence(classify(del.error)) : undefined}
        />
      )}
    </View>
  );
}

export function MemoBox({ id }: { id: number }) {
  const c = usePalette();
  const add = useWrite('task.memo.add', String(id));
  const why = useWhyNot('task.memo.add');
  const [text, setText] = useState('');
  const send = () => {
    if (!text.trim()) return;
    // The text stays until the daemon accepts it.
    add.mutateAsync({ task: id, text: text.trim() }).then(
      () => setText(''),
      () => {},
    );
  };
  return (
    <View style={{ gap: space.sm }}>
      <ImageBodyInput
        testID="memo-text"
        label="Memo"
        placeholder="Add a memo"
        titled={false}
        editable={!why}
        value={text}
        onChange={setText}
      />
      <Button testID="memo-send" label="Add memo" disabled={!!why || !text.trim()} onPress={send} />
      {why && <Text style={{ color: c.muted, fontSize: 12 }}>{why}</Text>}
    </View>
  );
}
