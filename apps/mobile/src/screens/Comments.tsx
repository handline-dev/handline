// A task's comments on the phone (docs/pc-app-gaps.md U11): add, edit and delete, each a `useWrite` that shows at once and rolls back with the daemon's reason.
import { useState } from 'react';
import { Pressable, Text, TextInput, View } from 'react-native';
import { isPerson } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import type { MemoFile, MemoReader } from '@handline/client';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { ImageBodyInput } from '#src/screens/ImageBodyInput.tsx';
import { Button, MenuItem, MenuSheet } from '#src/screens/parts.tsx';
import { CommentReaders } from '#src/screens/CommentReaders.tsx';
import { MemoFiles } from '#src/screens/MemoFiles.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';
import { PrBody } from '#src/screens/PrBody.tsx';

const small = { paddingVertical: space.sm, paddingHorizontal: space.md } as const;

/** One comment; on one's own, a ... opens Edit and Delete (as the web's MemoRow). Both show at once and roll back with the reason on a refusal; Delete asks first. */
export function CommentRow({
  task,
  agent,
  memo,
  when,
}: {
  task: number;
  agent: string;
  memo: { id: number; author: string; text: string; files?: MemoFile[]; readers?: MemoReader[] };
  when: string;
}) {
  const c = usePalette();
  const edit = useWrite('task.comment.edit', String(task));
  const del = useWrite('task.comment.delete', String(task));
  const offEdit = useOff('task.comment.edit');
  const offDelete = useOff('task.comment.delete');
  const [ask, setAsk] = useState<null | 'edit' | 'delete'>(null);
  const [text, setText] = useState('');
  const [menu, setMenu] = useState(false);
  const mine = isPerson(memo.author, useStore().me);
  const sending = memo.id < 0; // not yet numbered by the daemon
  const close = () => (setAsk(null), edit.reset(), del.reset());
  return (
    <View testID={`memo-${memo.id}`} style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: c.muted, fontSize: font.size.sm }}>{`${mine ? 'You' : memo.author} ${when}`}</Text>
        {ask === null && mine && (
          <Pressable
            testID={`memo-more-${memo.id}`}
            accessibilityRole="button"
            accessibilityLabel="Edit or delete this comment"
            disabled={sending || (!!offEdit && !!offDelete)}
            hitSlop={8}
            onPress={() => setMenu(true)}
            style={{ minHeight: 44, minWidth: 44, alignItems: 'flex-end', justifyContent: 'center', marginVertical: -space.sm }}
          >
            <Text style={{ color: c.muted, fontSize: font.size.lg, fontWeight: '700' }}>⋯</Text>
          </Pressable>
        )}
      </View>
      {ask === 'edit' ? null : <PrBody id={`memo-${memo.id}`} agent={agent} body={memo.text} />}
      {memo.files?.length ? <MemoFiles files={memo.files} /> : null}
      {memo.readers?.length ? <CommentReaders memo={memo.id} readers={memo.readers} /> : null}
      {menu && (
        <MenuSheet testID="memo-menu" onClose={() => setMenu(false)}>
          <MenuItem
            testID={`memo-edit-${memo.id}`}
            label="Edit"
            disabled={!!offEdit}
            note={offEdit}
            onPress={() => (setMenu(false), setText(memo.text), setAsk('edit'))}
          />
          <MenuItem
            testID={`memo-delete-${memo.id}`}
            label="Delete"
            danger
            disabled={!!offDelete}
            note={offDelete}
            onPress={() => (setMenu(false), setAsk('delete'))}
          />
        </MenuSheet>
      )}
      {ask === 'edit' && (
        <View style={{ gap: space.sm }}>
          <TextInput
            testID="memo-edit-text"
            accessibilityLabel="Comment"
            value={text}
            onChangeText={setText}
            multiline
            autoFocus
            style={{
              color: c.ink,
              borderWidth: 1,
              borderColor: !text.trim() || edit.isError ? c.block : c.line,
              borderRadius: radius.m,
              padding: space.md,
              minHeight: 72,
              textAlignVertical: 'top',
            }}
          />
          {!text.trim() || edit.isError ? (
            <Text testID="memo-edit-error" accessibilityRole="alert" style={{ color: c.block, fontSize: font.size.sm }}>
              {!text.trim() ? 'A comment needs text.' : sentence(classify(edit.error))}
            </Text>
          ) : null}
          <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: space.sm }}>
            <Button testID="memo-edit-cancel" label="Cancel" onPress={close} style={small} />
            <Button
              testID="memo-edit-save"
              kind="yes"
              label="Save"
              disabled={edit.isPending}
              onPress={() => void (text.trim() && edit.mutateAsync({ task, memo: memo.id, text: text.trim() }).then(close, () => {}))}
              style={small}
            />
          </View>
        </View>
      )}
      {ask === 'delete' && (
        <Sheet
          label="Delete this comment?"
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

export function CommentBox({ id }: { id: number }) {
  const c = usePalette();
  const add = useWrite('task.comment.add', String(id));
  const why = useOff('task.comment.add');
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
        label="Comment"
        placeholder="Add a comment…"
        titled={false}
        editable={!why}
        value={text}
        onChange={setText}
        send={
          <Button
            testID="memo-send"
            label="Comment"
            icon="send"
            kind={text.trim() ? 'yes' : 'plain'}
            disabled={!!why || !text.trim()}
            onPress={send}
          />
        }
      />
      {why && <Text style={{ color: c.muted, fontSize: font.size.sm }}>{why}</Text>}
    </View>
  );
}
