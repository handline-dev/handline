// One message of the chat as a bubble: its words and pictures, the request link and how far it got under it, and the retry of a refused send.
import { Pressable, Text, View } from 'react-native';
import { clockTime, isTaskDelivery, sideOf, tickLevel } from '@handline/app-core';
import { useChatActions } from '@handline/app-data';
import { useSelector, useStore } from '@handline/app-react';
import type { ChatMessage } from '@handline/client';
import { font, radius, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import { Stamp, useBubbleWidth } from '#src/screens/ChatLines.tsx';
import { CommandMessageScope } from '#src/screens/CommandCard.tsx';
import { Pictures } from '#src/screens/ChatPictures.tsx';
import { ChatActions } from '#src/screens/ChatActions.tsx';
import { PrBody } from '#src/screens/PrBody.tsx';
import { Icon } from '#src/screens/Icon.tsx';
import { Button, Link, Row } from '#src/screens/parts.tsx';

/** How far the person's message got, as ticks: not sent yet, sent, read. */
const TICKS = ['queued', 'delivered', 'answered'] as const;

export function Bubble(props: {
  m: ChatMessage;
  agent: string;
  picked: boolean;
  flashed: boolean;
  onRow: (y: number) => void;
  working: boolean;
  onPick: (m: ChatMessage) => void;
  onTask: (id: number) => void;
}) {
  const c = usePalette();
  const bubble = useBubbleWidth();
  const { m, agent } = props;
  const act = useChatActions(agent);
  const canSend = useSelector((s) => s.can('chat.send'));
  const side = sideOf(m, agent, useStore().me);
  const unnumbered = m.id < 0; // shown before the daemon numbered it, or refused
  const failed = m.state === 'failed';
  const mine = side === 'person' && !unnumbered && !failed;
  const person = side === 'person';
  const bg = person ? (failed ? c.panel : c.accent) : 'transparent';
  const sub = person && !failed ? '#ffffffcc' : c.muted;
  const ink = person && !failed ? '#fff' : undefined; // the person's words are white on the accent
  const body = m.body.replace(/!\[[^\]]*\]\(attachment:[0-9a-f]+\)/g, '').trim(); // a picture is drawn from its attachment, never as its link
  const stamp = (
    <Stamp
      m={m}
      time={props.picked ? clockTime(m.at) : null}
      ticks={
        side === 'person' && !failed
          ? m.landing === 'withdrawn'
            ? 'withdrawn'
            : m.receipt?.state === 'unknown'
              ? 'maybeRan'
              : TICKS[tickLevel(m)]
          : null
      }
    />
  );
  // Like the PC chat: the ticks sit beside the bubble at its bottom, and the time shows once the row is picked (PC shows it on hover).
  return (
    <Pressable
      testID={`msg-${m.id}`}
      disabled={unnumbered || failed}
      onLayout={(e) => props.onRow(e.nativeEvent.layout.y)}
      onPress={() => props.onPick(m)}
      style={{ gap: 2, alignItems: side === 'person' ? 'flex-end' : 'flex-start' }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: space.xs }}>
        <View
          style={{
            flexShrink: 1,
            maxWidth: bubble,
            gap: 4,
            padding: space.md,
            borderRadius: radius.l,
            backgroundColor: bg,
            borderWidth: props.picked || props.flashed || failed ? 1 : 0,
            borderColor: props.picked || props.flashed ? c.accent : failed ? c.block : c.line,
            opacity: unnumbered && !failed ? 0.6 : 1,
          }}
        >
          {props.flashed && (
            <Text testID={`found-${m.id}`} style={{ color: sub, fontSize: font.size.xs }}>
              Found by search
            </Text>
          )}
          {side === 'other' && (
            <Text style={{ color: c.muted, fontSize: font.size.xs }}>
              {isTaskDelivery(m) ? `Task T${m.tasks[0]!.id} handed to ${m.to}` : `${m.from} → ${m.to}`}
            </Text>
          )}
          {m.parent != null && <Text style={{ color: sub, fontSize: font.size.xs }}>↩ reply</Text>}
          {m.parent_item && (
            <Text testID={`quote-${m.id}`} numberOfLines={2} style={{ color: sub, fontSize: font.size.xs }}>
              ↩ {m.parent_item.text.replace(/\s+/g, ' ')}
            </Text>
          )}
          {body ? (
            <CommandMessageScope id={m.id} runs={m.runs}>
              <PrBody id={m.id} agent={agent} body={body} selectable ink={ink} />
            </CommandMessageScope>
          ) : null}
          {m.attachments?.length ? (
            <Pictures
              deletable
              items={m.attachments.map((a) => ({ ...a, removed: m.parts?.some((x) => 'image' in x && x.image.id === a.id && x.removed) }))}
              to={agent}
              onSend={canSend ? (words, attachments) => act.send(words, { attachments }) : undefined}
            />
          ) : null}
          {m.actions?.length ? <ChatActions id={m.id} actions={m.actions} /> : null}
          {failed && (
            <View accessibilityRole="alert" style={{ gap: space.xs }}>
              <Row>
                <Icon name="failed" decorative size={14} color={c.block} />
                <Text style={{ color: c.block, fontSize: font.size.sm, flex: 1 }}>{m.sending_reason}</Text>
              </Row>
              <Row>
                <Button testID={`retry-${m.id}`} label="Retry" onPress={() => act.retry(m.id)} />
                <Button testID={`discard-${m.id}`} label="Discard" kind="no" onPress={() => act.discard(m.id)} />
              </Row>
            </View>
          )}
          {mine && m.receipt?.state === 'unknown' && (
            <Text testID={`unknown-${m.id}`} style={{ color: c.block, fontSize: font.size.sm }}>
              Lost after it was written. It may or may not have run, and it is not sent again. Send it as a new message if you want it
              repeated.
            </Text>
          )}
          {mine && (m.receipt ? m.receipt.state === 'queued' : m.landing === 'queued' && props.working) && (
            <Row style={{ gap: space.lg }}>
              <Link testID={`now-${m.id}`} color={ink} label="Send now" onPress={() => act.sendNow(m.id)} />
            </Row>
          )}
        </View>
        {side !== 'person' && stamp}
      </View>
      {side === 'person' && (
        <Row style={{ gap: space.sm }}>
          {stamp}
          {!failed && TICKS[tickLevel(m)] === 'queued' && m.landing !== 'withdrawn' ? (
            <Text testID={`sending-${m.id}`} style={{ color: c.muted, fontSize: font.size.sm }}>
              Sending
            </Text>
          ) : null}
          {m.request_task != null ? (
            <Pressable testID={`request-chip-${m.id}`} accessibilityRole="link" hitSlop={8} onPress={() => props.onTask(m.request_task!)}>
              <Text style={{ color: c.accent, fontSize: font.size.sm }}>Taken as T{m.request_task}</Text>
            </Pressable>
          ) : m.no_work ? (
            <Text testID={`request-chip-${m.id}`} style={{ color: c.muted, fontSize: font.size.sm }}>
              Nothing to do: {m.no_work}
            </Text>
          ) : null}
        </Row>
      )}
    </Pressable>
  );
}
