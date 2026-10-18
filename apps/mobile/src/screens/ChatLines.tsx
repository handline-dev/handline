// The lines of the chat that are not messages: the agent's reply, its prompt and session lines.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { TranscriptItem } from '@handline/client';
import { clockTime, sessionLine } from '@handline/app-core';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Sheet } from './TaskActions.tsx';

/** A transcript line that is not a message. */
export function Line({ i, onReply }: { i: TranscriptItem; onReply?: (i: TranscriptItem) => void }) {
  const c = usePalette();
  const small = { color: c.muted, fontSize: 12 } as const;
  if (i.kind === 'reply')
    return (
      <View testID={`reply-${i.id}`} style={{ alignSelf: 'flex-start', maxWidth: '88%', padding: space.md }}>
        <Text style={{ color: c.ink }}>{i.text}</Text>
        <Text style={small}>{clockTime(i.at)}</Text>
        {onReply && (
          <Pressable
            testID={`reply-to-${i.id}`}
            accessibilityRole="button"
            accessibilityLabel="Reply to this"
            hitSlop={8}
            onPress={() => onReply(i)}
          >
            <Text style={{ color: c.accent, fontSize: 12, fontWeight: '600' }}>↩ Reply</Text>
          </Pressable>
        )}
      </View>
    );
  if (i.kind === 'prompt') {
    const p = i.prompt;
    return (
      <Text style={small} numberOfLines={2}>
        {i.open ? '⏳' : '✓'} {p?.kind === 'permission' ? `Asked permission: ${p.tool ?? ''}` : `Asked: ${p?.kind ?? 'a dialog'}`}
        {p?.command ? ` ${p.command}` : ''} · {clockTime(i.at)}
      </Text>
    );
  }
  if (i.kind === 'session') return <SessionMark i={i} />;
  return null;
}

/** Where a session started; one that was given a handoff opens it in a sheet on a tap. */
export function SessionMark({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const { reason, handoff } = sessionLine(i);
  const mark = (
    <Text testID={`session-${i.id}`} style={{ color: c.muted, fontSize: 12, textAlign: 'center' }}>
      ── {reason} · {clockTime(i.at)}
      {handoff ? ' · Handoff' : ''} ──
    </Text>
  );
  if (!handoff) return mark;
  return (
    <View>
      <Pressable
        testID={`session-open-${i.id}`}
        accessibilityRole="button"
        style={{ minHeight: 44, justifyContent: 'center' }}
        onPress={() => setOpen(true)}
      >
        {mark}
      </Pressable>
      {open && (
        <Sheet label="Handoff" onCancel={() => setOpen(false)}>
          <Text testID="handoff-text" style={{ color: c.ink }}>
            {handoff}
          </Text>
        </Sheet>
      )}
    </View>
  );
}
