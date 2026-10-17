import { useState } from 'react';
import { Text, View } from 'react-native';
import { taskRef } from '@handline/app-core';
import type { TaskDetail } from '@handline/client';
import { classify, sentence, useWrite } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Input, Sheet } from './TaskActions.tsx';
import { Pill } from './parts.tsx';

/** Ask about a task: pick whom (the daemon's people for it) and type the question; the sheet keeps both until the daemon accepts. */
export function AskAbout({ t, onClose }: { t: TaskDetail; onClose: () => void }) {
  const c = usePalette();
  const ask = useWrite('task.ask');
  const people = [...new Set([t.to, ...t.people.recent, ...t.people.team, ...t.people.others])];
  const [to, setTo] = useState(t.to);
  const [text, setText] = useState('');
  const [missing, setMissing] = useState('');
  const send = () => {
    if (!to) return setMissing('Pick whom to ask.');
    if (!text.trim()) return setMissing('Type the question.');
    setMissing('');
    ask.mutateAsync({ task: t.id, to, text: text.trim() }).then(onClose, () => {});
  };
  return (
    <Sheet
      label={`ask about ${taskRef(t.id)}`}
      submit="Ask"
      busy={ask.isPending}
      onCancel={onClose}
      onSubmit={send}
      error={missing || (ask.isError ? sentence(classify(ask.error)) : undefined)}
    >
      <Text style={{ color: c.muted, fontWeight: '600' }}>Whom</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {people.map((a) => (
          <Pill role="radio" key={a} on={to === a} label={a} onPress={() => setTo(a)} testID={`ask-to-${a}`} />
        ))}
      </View>
      <Input testID="ask-text" label="Question" value={text} onChange={(v) => (setText(v), setMissing(''))} multiline />
    </Sheet>
  );
}
