// The lines of the chat that are not messages: the agent's reply, its prompt and session lines.
import { useState } from 'react';
import * as Clipboard from 'expo-clipboard';
import { Pressable, Text, View, useWindowDimensions } from 'react-native';
import type { Proposal, TranscriptItem } from '@handline/client';
import { ago, clockTime, isPerson, sessionLine, taskRow } from '@handline/app-core';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Icon, iconLabel, type IconName } from '#src/screens/Icon.tsx';
import { CommandMessageScope } from '#src/screens/CommandCard.tsx';
import { PrBody } from '#src/screens/PrBody.tsx';
import { Sheet } from '#src/screens/TaskActions.tsx';
import { MenuItem, MenuSheet } from '#src/screens/parts.tsx';
import { LONG_PRESS_MS } from '#src/screens/ChatSend.tsx';

/** The time of a message and, for the person's own, how far it got, as a small column beside its bubble. */
export function Stamp({ m, time, ticks }: { m: { id: number }; time: string | null; ticks: IconName | null }) {
  const c = usePalette();
  return (
    <View style={{ alignItems: 'flex-end', flexShrink: 0 }}>
      {time ? <Text style={{ color: c.muted, fontSize: font.size.xs }}>{time}</Text> : null}
      {ticks ? (
        <View testID={`landing-${m.id}`} accessible accessibilityLabel={iconLabel(ticks)} style={{ alignItems: 'flex-end' }}>
          <Icon name={ticks} decorative size={16} color={c.ink} />
          {ticks === 'withdrawn' ? <Text style={{ color: c.muted, fontSize: font.size.xs }}>{iconLabel(ticks)}</Text> : null}
        </View>
      ) : null}
    </View>
  );
}

/** The widest a chat bubble gets, in points. A percentage here lets Android measure the text at one width and draw it at another, so lines overlap. */
export function useBubbleWidth(): number {
  return Math.floor(useWindowDimensions().width * 0.88);
}

/** A transcript line that is not a message. */
export function Line(props: {
  i: TranscriptItem;
  agent: string;
  me?: string;
  titles?: Map<number, string>;
  onReply?: (i: TranscriptItem) => void;
  onTask?: (task: number) => void;
}) {
  const { i, agent, onReply, onTask } = props;
  if (i.kind === 'reply') return <ReplyLine i={i} agent={agent} onReply={onReply} />;
  if (i.kind === 'prompt') return <PromptLine i={i} />;
  if (i.kind === 'session') return <SessionMark i={i} />;
  if (i.kind === 'task_batch' && i.task_batch) return <BatchCard i={i} onTask={onTask} />;
  if (i.kind === 'task_event' && i.task_event)
    return <TaskCard i={i} agent={agent} me={props.me ?? ''} titles={props.titles} onTask={onTask} />;
  return null;
}

/** A task opened, changed, done or withdrawn as one quiet line: what happened, the task and its title; a tap opens the task. */
export function TaskCard({
  i,
  agent,
  me,
  titles,
  onTask,
}: {
  i: TranscriptItem;
  agent: string;
  me: string;
  titles?: Map<number, string>;
  onTask?: (task: number) => void;
}) {
  const c = usePalette();
  const row = taskRow(i, titles);
  const who = row.who && row.who !== agent ? ` · by ${isPerson(row.who, me) ? 'you' : row.who}` : '';
  return (
    <Pressable
      testID={`task-event-${i.id}`}
      accessibilityRole="button"
      accessibilityLabel={`Open T${row.task}`}
      style={{ minHeight: 44, justifyContent: 'center' }}
      onPress={() => onTask?.(row.task)}
    >
      <Text numberOfLines={1} style={{ color: c.muted, fontSize: font.size.sm }}>
        {row.label} · T{row.task}
        {row.title ? ` ${row.title}` : ''}
        {who} · {ago(i.at)}
      </Text>
    </Pressable>
  );
}

/** A batch discard as one card: a line per task with its title and reason; a tap opens the task, where it can be reopened. */
export function BatchCard({ i, onTask }: { i: TranscriptItem; onTask?: (task: number) => void }) {
  const c = usePalette();
  const b = i.task_batch;
  if (!b) return null;
  return (
    <View testID={`batch-${i.id}`} style={{ borderWidth: 1, borderColor: c.line, borderRadius: 8, padding: space.md, gap: space.xs }}>
      <Text style={{ color: c.ink, fontWeight: '700' }}>
        {b.who} discarded {b.tasks.length} {b.tasks.length === 1 ? 'task' : 'tasks'} · {clockTime(i.at)}
      </Text>
      {b.tasks.map((t) => (
        <Pressable
          key={t.task}
          testID={`batch-task-${t.task}`}
          accessibilityRole="button"
          accessibilityLabel={`Open T${t.task}`}
          style={{ minHeight: 44, justifyContent: 'center' }}
          onPress={() => onTask?.(t.task)}
        >
          <Text style={{ color: c.accent, fontWeight: '600' }}>
            T{t.task} {t.title}
          </Text>
          {t.reason ? <Text style={{ color: c.muted, fontSize: font.size.sm }}>{t.reason}</Text> : null}
        </Pressable>
      ))}
    </View>
  );
}

/** Where a session started; one that was given a handoff opens it in a sheet on a tap. */
export function SessionMark({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  const { reason, handoff } = sessionLine(i);
  const mark = (
    <Text testID={`session-${i.id}`} style={{ color: c.muted, fontSize: font.size.sm, textAlign: 'center' }}>
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

/** The agent's words: plain text on the page. A press and hold offers to quote the whole message or copy it. */
function ReplyLine({ i, agent, onReply }: { i: TranscriptItem; agent: string; onReply?: (i: TranscriptItem) => void }) {
  const [menu, setMenu] = useState(false);
  return (
    <Pressable
      testID={`reply-${i.id}`}
      accessibilityHint="Hold to quote or copy"
      delayLongPress={LONG_PRESS_MS}
      onLongPress={() => setMenu(true)}
      style={{ padding: space.md }}
    >
      <CommandMessageScope id={i.id} reply runs={i.runs}>
        <PrBody id={`reply-${i.id}`} agent={agent} body={i.text ?? ''} />
      </CommandMessageScope>
      {menu && (
        <MenuSheet testID={`reply-menu-${i.id}`} onClose={() => setMenu(false)}>
          {onReply && <MenuItem testID={`reply-to-${i.id}`} label="Quote" icon="reference" onPress={() => (setMenu(false), onReply(i))} />}
          <MenuItem
            testID={`reply-copy-${i.id}`}
            label="Copy"
            icon="copy"
            onPress={() => (setMenu(false), void Clipboard.setStringAsync(i.text ?? ''))}
          />
        </MenuSheet>
      )}
    </Pressable>
  );
}

/** A request the agent made, as one grey line like "Allow Bash go vet ./… · 9m"; a tap opens the whole command. */
function PromptLine({ i }: { i: TranscriptItem }) {
  const c = usePalette();
  const [full, setFull] = useState(false);
  const p = i.prompt;
  const what = p?.kind === 'permission' ? `Allow ${p.tool ?? ''}` : `Asked: ${p?.kind ?? 'a dialog'}`;
  return (
    <Pressable testID={`prompt-${i.id}`} accessibilityRole="button" accessibilityState={{ expanded: full }} onPress={() => setFull(!full)}>
      <Text style={{ color: c.muted, fontSize: font.size.sm }} numberOfLines={full ? undefined : 1}>
        {[what, p?.command].filter(Boolean).join(' ')} · {ago(i.at)}
      </Text>
    </Pressable>
  );
}

/** A decided HR proposal: one line at the time it was settled. */
export function Decided({ p }: { p: Proposal }) {
  const c = usePalette();
  return (
    <Text testID="decided" style={{ color: c.muted, fontSize: font.size.sm }}>
      {p.title} · {p.state === 'rejected' ? 'Rejected' : 'Applied'} by you · {p.settled ? clockTime(p.settled) : ''}
    </Text>
  );
}
