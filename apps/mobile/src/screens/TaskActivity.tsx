// The Activity list of a task page: its comments and the events in words, and the one comment box under them.
import { useState } from 'react';
import { Text, View } from 'react-native';
import { activityOf, ago, foldEvents } from '@handline/app-core';
import type { TaskDetail } from '@handline/client';
import { useMemos } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Button } from '#src/screens/parts.tsx';
import { CommentBox, CommentRow } from '#src/screens/Comments.tsx';
import { EventNote } from '#src/screens/EventNote.tsx';
import { Section } from '#src/screens/TaskHead.tsx';

/** One list of the comments and the events in words (older events fold), and the one comment box under it. */
export function Activity({ t }: { t: TaskDetail }) {
  const c = usePalette();
  const memos = useMemos(t.id).data?.memos ?? t.memos; // the memo list is the one a memo write edits at once
  const [all, setAll] = useState(false);
  const { items, hidden } = foldEvents(activityOf(memos, t.timeline), all);
  return (
    <Section title="Activity">
      {hidden ? (
        <Button testID="activity-more" label={`Show ${hidden} earlier ${hidden === 1 ? 'event' : 'events'}`} onPress={() => setAll(true)} />
      ) : null}
      {items.map((i, n) => {
        const m = i.kind === 'comment' ? memos.find((x) => x.id === i.memo) : undefined;
        return m ? (
          <CommentRow key={`${n}-${m.id}`} task={t.id} agent={t.to} memo={m} when={ago(m.at)} />
        ) : (
          <View key={`${n}-${i.line}`} style={{ gap: space.xs }}>
            <Text style={{ color: c.muted }}>{`${i.line} · ${ago(i.at)}`}</Text>
            {i.note ? <EventNote id={`${n}`} agent={t.to} note={i.note} /> : null}
          </View>
        );
      })}
      <CommentBox id={t.id} />
    </Section>
  );
}
