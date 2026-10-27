// The small button under a founder's comment that told agents: how many have read it, a clock while one is held, and a sheet with each reader (the web's CommentReaders).
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { MemoReader } from '@handline/client';
import { ago } from '@handline/app-core';
import { classify, sentence, useOff, useWrite } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon, type IconName } from '#src/screens/Icon.tsx';
import { Button } from '#src/screens/parts.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';

const STATE: Record<MemoReader['state'], { icon: IconName; text: (r: MemoReader) => string }> = {
  read: { icon: 'answered', text: (r) => (r.at ? `${ago(r.at)} ago` : 'read') },
  sent: { icon: 'delivered', text: () => 'sent, not read' },
  held: { icon: 'waiting', text: (r) => `waits for the next pickup, in about ${r.minutes ?? 1} min` },
};

/** Nothing for a comment every reader has read: the button goes when there is nothing left to learn. */
export function CommentReaders({ memo, readers }: { memo: number; readers: MemoReader[] }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const send = useWrite('task.comment.send_now');
  const off = useOff('task.comment.send_now');
  const read = readers.filter((r) => r.state === 'read').length;
  if (!readers.length || read === readers.length) return null;
  const held = readers.some((r) => r.state === 'held');
  return (
    <>
      <Pressable
        testID={`memo-readers-${memo}`}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen(true)}
        style={{ alignSelf: 'flex-start', flexDirection: 'row', alignItems: 'center', gap: space.xs, minHeight: 32 }}
      >
        {held ? <Icon name="waiting" color={c.muted} size={12} decorative /> : null}
        <Text style={{ color: c.muted, fontSize: font.size.label }}>{`${read}/${readers.length} read`}</Text>
      </Pressable>
      {open ? (
        <Sheet
          id="readers"
          label="Who has read this"
          onCancel={() => (setOpen(false), send.reset())}
          error={send.isError ? sentence(classify(send.error)) : undefined}
        >
          {readers.map((r) => (
            <View key={r.agent} testID={`reader-${r.agent}`} style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, minHeight: 44 }}>
              <Icon name={STATE[r.state].icon} color={c.muted} size={16} decorative />
              <Text style={{ color: c.ink }}>{r.agent}</Text>
              <Text style={{ color: c.muted, flex: 1, textAlign: 'right' }}>{STATE[r.state].text(r)}</Text>
              {r.state === 'held' ? (
                <Button
                  testID={`reader-send-${r.agent}`}
                  kind="yes"
                  label="Send now"
                  disabled={!!off || send.isPending}
                  onPress={() => send.mutate({ memo, agent: r.agent })}
                />
              ) : null}
            </View>
          ))}
        </Sheet>
      ) : null}
    </>
  );
}
