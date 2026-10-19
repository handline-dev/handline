// The lines of the chat that are not messages: the agent's reply, its prompt and session lines.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { Proposal, TranscriptItem } from '@handline/client';
import { clockTime, sessionLine } from '@handline/app-core';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Icon, iconLabel, type IconName } from './Icon.tsx';
import { Markdown } from './Markdown.tsx';
import { Sheet } from './TaskActions.tsx';

/** The time of a message and, for the person's own, how far it got, as a small column beside its bubble. */
export function Stamp({ m, time, ticks }: { m: { id: number }; time: string | null; ticks: IconName | null }) {
  const c = usePalette();
  return (
    <View style={{ alignItems: 'flex-end', flexShrink: 0 }}>
      {time ? <Text style={{ color: c.muted, fontSize: 11 }}>{time}</Text> : null}
      {ticks ? (
        <View testID={`landing-${m.id}`} accessible accessibilityLabel={iconLabel(ticks)}>
          <Icon name={ticks} decorative size={14} color={c.muted} />
        </View>
      ) : null}
    </View>
  );
}

/** A transcript line that is not a message. */
export function Line({ i, onReply }: { i: TranscriptItem; onReply?: (i: TranscriptItem) => void }) {
  const c = usePalette();
  const small = { color: c.muted, fontSize: 12 } as const;
  if (i.kind === 'reply')
    return (
      <View testID={`reply-${i.id}`} style={{ flexDirection: 'row', alignItems: 'flex-end', gap: space.xs }}>
        <View style={{ flex: 1, padding: space.md }}>
          <Markdown text={i.text ?? ''} breaks />
          {onReply && (
            <Pressable
              testID={`reply-to-${i.id}`}
              accessibilityRole="button"
              accessibilityLabel="Reply to this"
              hitSlop={8}
              onPress={() => onReply(i)}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs }}>
                <Icon name="reference" decorative size={14} color={c.accent} />
                <Text style={{ color: c.accent, fontSize: 12, fontWeight: '600' }}>Reply</Text>
              </View>
            </Pressable>
          )}
        </View>
        <Stamp m={i} time={clockTime(i.at)} ticks={null} />
      </View>
    );
  if (i.kind === 'prompt') {
    const p = i.prompt;
    return (
      <Text style={small} numberOfLines={2}>
        <Icon name={i.open ? 'waiting' : 'delivered'} label={i.open ? 'Open' : 'Answered'} size={12} color={c.muted} /> {p?.kind === 'permission' ? `Asked permission: ${p.tool ?? ''}` : `Asked: ${p?.kind ?? 'a dialog'}`}
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

/** A decided HR proposal: one line at the time it was settled. */
export function Decided({ p }: { p: Proposal }) {
  const c = usePalette();
  return (
    <Text testID="decided" style={{ color: c.muted, fontSize: 12 }}>
      {p.title} · {p.state === 'rejected' ? 'Rejected' : 'Applied'} by you · {p.settled ? clockTime(p.settled) : ''}
    </Text>
  );
}
