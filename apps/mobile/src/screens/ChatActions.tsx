// The one-tap buttons the daemon puts on a message that waits on a decision about its task (ChatMessage.actions). A tap calls the verb the
// action names with its params; an action that `needs` a note asks for it first. The daemon takes the buttons away once the task changes.
import { useState } from 'react';
import { KeyboardAvoidingView, Modal, Platform, Text, View } from 'react-native';
import { useStore } from '@handline/app-react';
import type { ChatAction } from '@handline/client';
import { classify, sentence } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';
import { Input, Sheet } from '#src/screens/TaskActions.tsx';

export function ChatActions({ id, actions }: { id: number; actions: ChatAction[] }) {
  const c = usePalette();
  const { client } = useStore();
  const [asking, setAsking] = useState<ChatAction | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const send = (a: ChatAction, extra: Record<string, unknown> = {}) => {
    setBusy(true);
    setFailure('');
    client
      .call(a.verb as 'task.done' | 'task.handoff', { task: a.task, ...a.params, ...extra } as never)
      .then(() => setAsking(null))
      .catch((e: unknown) => setFailure(sentence(classify(e))))
      .finally(() => setBusy(false));
  };
  const tap = (a: ChatAction) => {
    if (!a.needs) return send(a);
    setNote('');
    setAsking(a);
  };
  return (
    <View style={{ gap: space.xs }}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {actions.map((a) => (
          <Button
            key={a.key}
            testID={`action-${id}-${a.key}`}
            kind={a.key === 'ok' ? 'yes' : 'no'}
            label={a.label}
            disabled={busy}
            onPress={() => tap(a)}
          />
        ))}
      </View>
      {failure ? (
        <Text testID={`action-error-${id}`} accessibilityRole="alert" style={{ color: c.block, fontSize: 12 }}>
          {failure}
        </Text>
      ) : null}
      {asking ? (
        // Out of the narrow bubble and above the keyboard, so the submit and cancel buttons stay in reach.
        <Modal transparent visible animationType="fade" onRequestClose={() => setAsking(null)}>
          <KeyboardAvoidingView
            behavior={Platform.OS === 'ios' ? 'padding' : undefined}
            style={{ flex: 1, justifyContent: 'flex-end', padding: space.md, backgroundColor: 'rgba(0,0,0,0.35)' }}
          >
            <Sheet
              label={asking.label}
              submit={asking.label}
              kind="no"
              busy={busy}
              onCancel={() => setAsking(null)}
              onSubmit={() => note.trim() && send(asking, { [asking.needs!]: note.trim() })}
              error={failure || undefined}
            >
              <Input testID={`action-note-${id}`} label="Note" value={note} onChange={setNote} />
            </Sheet>
          </KeyboardAvoidingView>
        </Modal>
      ) : null}
    </View>
  );
}
