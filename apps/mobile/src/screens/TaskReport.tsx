// Accept or reject a task's report (docs/pc-app-gaps.md R3), for its opener. Both are decided writes: the row changes when the daemon
// says so. Reject needs the one line that says what is missing and keeps the sheet with it when refused.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { taskRef } from '@handline/app-core';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Button } from './parts.tsx';
import { Input, Sheet, useWhyNot } from './TaskActions.tsx';

export function ReportVerbs({ t }: { t: TaskDetail }) {
  const c = usePalette();
  const accept = useWrite('task.accept');
  const reject = useWrite('task.reject');
  const offAccept = useWhyNot('task.accept');
  const offReject = useWhyNot('task.reject');
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState('');
  const [empty, setEmpty] = useState(false);
  const send = () => {
    if (!note.trim()) return setEmpty(true);
    reject.mutateAsync({ task: t.id, note: note.trim() }).then(
      () => setRejecting(false),
      () => {},
    );
  };
  const said = accept.isError ? sentence(classify(accept.error)) : '';
  return (
    <View style={{ gap: space.sm }}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        <Button
          testID="verb-accept"
          kind="yes"
          label={accept.isPending ? 'Accepting…' : 'Accept'}
          disabled={!!offAccept || accept.isPending || rejecting}
          onPress={() => void accept.mutateAsync({ task: t.id }).catch(() => {})}
        />
        <Button testID="verb-reject" kind="no" label="Reject…" disabled={!!offReject || rejecting} onPress={() => setRejecting(true)} />
      </View>
      {offAccept ? <Text style={{ color: c.muted, fontSize: 12 }}>{offAccept}</Text> : null}
      {said ? (
        <Text testID="accept-error" accessibilityRole="alert" style={{ color: c.block }}>
          {said}
        </Text>
      ) : null}
      {rejecting ? (
        <Sheet
          label={`reject the report on ${taskRef(t.id)}: what is missing?`}
          submit="Send back"
          kind="no"
          busy={reject.isPending}
          onCancel={() => setRejecting(false)}
          onSubmit={send}
          error={empty && !note.trim() ? 'A note is required.' : reject.isError ? sentence(classify(reject.error)) : undefined}
        >
          <Input testID="reject-note" label="Note" value={note} onChange={(v) => (setNote(v), setEmpty(false))} />
        </Sheet>
      ) : null}
    </View>
  );
}
