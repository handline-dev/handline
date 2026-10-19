// The one-tap buttons the daemon puts on a message that waits on a decision about its task (ChatMessage.actions). A tap calls the verb the
// action names with its params; an action that `needs` a note asks for it first. The daemon takes the buttons away once the task changes.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { useStore } from '@handline/app-react';
import type { ChatAction } from '@handline/client';
import { classify, sentence } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';
import { Input, Sheet } from './TaskActions.tsx';

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
      .call(a.verb, { task: a.task, ...a.params, ...extra } as never)
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
            kind={a.key === 'accept' || a.key === 'ok' ? 'yes' : 'no'}
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
      ) : null}
    </View>
  );
}
