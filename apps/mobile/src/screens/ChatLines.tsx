// The lines of the chat that are not messages: the agent's reply and its tool, prompt, task and session lines.
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import type { TranscriptItem } from '@handline/client';
import { sessionLine } from '@handline/app-core';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Sheet } from './TaskActions.tsx';

export const clock = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
};

/** A transcript line that is not a message. */
export function Line({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const small = { color: c.muted, fontSize: 12 } as const;
  if (i.kind === 'reply')
    return (
      <View testID={`reply-${i.id}`} style={{ alignSelf: 'flex-start', maxWidth: '88%', padding: space.md }}>
        <Text style={{ color: c.ink }}>{i.text}</Text>
        <Text style={small}>{clock(i.at)}</Text>
      </View>
    );
  if (i.kind === 'tool') return <ToolLine i={i} />;
  if (i.kind === 'prompt') {
    const p = i.prompt;
    return (
      <Text style={small} numberOfLines={2}>
        {i.open ? '⏳' : '✓'} {p?.kind === 'permission' ? `Asked permission: ${p.tool ?? ''}` : `Asked: ${p?.kind ?? 'a dialog'}`}
        {p?.command ? ` ${p.command}` : ''} · {clock(i.at)}
      </Text>
    );
  }
  if (i.kind === 'session') return <SessionMark i={i} />;
  const e = i.task_event;
  return (
    <Text style={small} numberOfLines={2}>
      ▣ T{e?.task} {e?.event ?? 'changed'}
      {e?.evidence ? ` · ${e.evidence}` : ''} · {clock(i.at)}
    </Text>
  );
}

/** A tool line: the command, and what it printed, in full, in a sheet behind a tap. */
function ToolLine({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const cut = i.dropped_lines ?? 0;
  const line = (
    <Text testID={`tool-${i.id}`} style={{ color: c.muted, fontSize: 12 }} numberOfLines={2}>
      ⚙ {i.tool}
      {i.text ? ` ${i.text}` : ''}
      {i.output ? ' · Output' : ''}
      {!i.output && cut > 0 ? ` · ${cut} more line${cut === 1 ? '' : 's'} let go` : ''}
    </Text>
  );
  if (!i.output) return line;
  return (
    <View>
      <Pressable
        testID={`tool-open-${i.id}`}
        accessibilityRole="button"
        style={{ minHeight: 44, justifyContent: 'center' }}
        onPress={() => setOpen(true)}
      >
        {line}
      </Pressable>
      {open && (
        <Sheet label="Output" onCancel={() => setOpen(false)}>
          <Text testID="tool-output" style={{ color: c.ink, fontFamily: 'Menlo' }}>
            {i.output}
          </Text>
          {cut > 0 && (
            <Text testID="tool-cut" style={{ color: c.muted, fontSize: 12 }}>
              {cut} more line{cut === 1 ? '' : 's'} let go
            </Text>
          )}
        </Sheet>
      )}
    </View>
  );
}

/** Where a session started; one that was given a handoff opens it in a sheet on a tap. */
function SessionMark({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const { reason, handoff } = sessionLine(i);
  const mark = (
    <Text testID={`session-${i.id}`} style={{ color: c.muted, fontSize: 12, textAlign: 'center' }}>
      ── {reason} · {clock(i.at)}
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
