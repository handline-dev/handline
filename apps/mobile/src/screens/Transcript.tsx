// What a structured agent did, in place of its terminal: its turns with text, tool calls and results (collapsed until tapped),
// permission cards, what each turn cost, and errors. It follows the stream and stays at the end.
import { useEffect, useRef, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { usePrompts, type Entry, type Transcript as TranscriptState, type Turn } from '@handline/app-data';
import { space } from '@handline/tokens';
import { usePalette } from '../theme.ts';
import { Loadable } from './Loadable.tsx';
import { PromptCard } from './PromptCard.tsx';
import { Button } from './parts.tsx';

const oneLine = (s: string) => s.replace(/\s+/g, ' ').trim().slice(0, 80);
const inputLine = (input: Record<string, unknown>) => {
  const first = Object.values(input).find((v) => typeof v === 'string');
  return oneLine(typeof first === 'string' ? first : JSON.stringify(input));
};

function Fold(props: { id: string; head: string; body: string; tint?: string }) {
  const c = usePalette();
  const [open, setOpen] = useState(false);
  return (
    <Pressable testID={props.id} onPress={() => setOpen(!open)} style={{ paddingVertical: 2 }}>
      <Text style={{ color: props.tint ?? c.muted, fontFamily: 'Menlo', fontSize: 12 }}>
        {open ? '▾' : '▸'} {props.head}
      </Text>
      {open && <Text style={{ color: c.ink, fontFamily: 'Menlo', fontSize: 11, paddingLeft: space.md }}>{props.body}</Text>}
    </Pressable>
  );
}

function Tool(props: { e: Extract<Entry, { type: 'tool' }>; agent: string; held: Set<string> }) {
  const c = usePalette();
  const { e } = props;
  const result = e.result;
  const card = e.permission;
  // The card only while the daemon still holds the prompt; a client that has missed the answer waits for prompt.list.
  const asking = card?.open && props.held.has(e.id);
  return (
    <View testID={`tool-${e.id}`}>
      <Fold id={`tr-${e.seq}`} head={`${e.tool ?? 'tool'} ${inputLine(e.input)}`} body={JSON.stringify(e.input, null, 2)} />
      {asking && card && <PromptCard agent={props.agent} prompt={card.prompt} onStale={() => {}} />}
      {e.answer && (
        <Text testID={`answer-${e.id}`} style={{ color: c.muted, fontSize: 12 }}>
          {e.answer.behavior === 'allow' ? 'Allowed' : 'Denied'} by {e.answer.by}
          {e.answer.message ? `: ${e.answer.message}` : ''}
        </Text>
      )}
      {result && (
        <Fold
          id={`result-${e.id}`}
          head={`${e.tool ?? 'tool'} ${result.ok ? 'returned' : 'failed'}: ${oneLine(result.text)}`}
          body={result.text + (result.truncated ? '\n… cut' : '')}
          tint={result.ok ? c.muted : c.block}
        />
      )}
    </View>
  );
}

function TurnView(props: { turn: Turn; agent: string; held: Set<string> }) {
  const c = usePalette();
  const { turn: t } = props;
  return (
    <View testID={`turn-${t.seq}`} style={{ gap: space.sm }}>
      {t.message && (
        <Text style={{ color: c.ink, fontWeight: '600' }}>
          {t.message.from ?? 'someone'}: {t.message.text}
        </Text>
      )}
      {t.entries.map((e) =>
        e.type === 'text' ? (
          <Text key={e.seq} style={{ color: c.ink }}>
            {e.text}
          </Text>
        ) : e.type === 'tool' ? (
          <Tool key={e.seq} e={e} agent={props.agent} held={props.held} />
        ) : e.type === 'error' ? (
          <Text key={e.seq} testID={`error-${e.seq}`} style={{ color: c.block, fontSize: 12 }}>
            {e.code}: {e.text}
          </Text>
        ) : (
          <Text key={e.seq} testID="gap" style={{ color: c.muted, fontSize: 12 }}>
            Earlier events are no longer kept.
          </Text>
        ),
      )}
      {t.end && (
        <Text testID={`end-${t.seq}`} style={{ color: c.muted, fontSize: 12 }}>
          {t.end.reason} · ${t.end.cost_usd.toFixed(2)} · {(t.end.duration_ms / 1000).toFixed(1)} s
        </Text>
      )}
      {t.running && (
        <Text testID="running" style={{ color: c.muted, fontSize: 12 }}>
          working…
        </Text>
      )}
    </View>
  );
}

export function Transcript(props: { agent: string; transcript: TranscriptState }) {
  const c = usePalette();
  const t = props.transcript;
  const list = usePrompts(props.agent);
  const prompts = list.data;
  const scroll = useRef<ScrollView>(null);
  // A card the stream opens or closes: prompt.list says whether the daemon still holds it, so it is read again.
  const opened = t.turns.flatMap((u) => u.entries.flatMap((e) => (e.type === 'tool' && e.permission?.open ? [e.id] : []))).join();
  const refetchPrompts = list.refetch;
  useEffect(() => void refetchPrompts(), [opened, refetchPrompts]);
  const held = new Set((prompts?.prompts ?? []).map((p) => p.id));
  return (
    <Loadable
      query={{
        isPending: t.status === 'loading',
        isFetching: false,
        isError: t.status === 'error',
        error: t.error,
        data: t.status === 'ready' ? t.turns : undefined,
        refetch: t.refetch,
      }}
      served
      skeleton="terminal"
      label="the transcript"
      empty=""
    >
      <ScrollView
        ref={scroll}
        testID="transcript"
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: space.md, gap: space.md }}
        onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: false })}
      >
        {!!t.olderError && (
          <Text testID="tr-older-error" style={{ color: c.block, fontSize: 12 }}>
            Could not load older events.
          </Text>
        )}
        {t.hasOlder && (
          <Button testID="tr-older" label={t.loadingOlder ? 'Loading…' : 'Load older'} disabled={t.loadingOlder} onPress={t.loadOlder} />
        )}
        {t.turns.length === 0 && <Text style={{ color: c.muted }}>Nothing yet.</Text>}
        {t.turns.map((turn) => (
          <TurnView key={turn.seq} turn={turn} agent={props.agent} held={held} />
        ))}
      </ScrollView>
    </Loadable>
  );
}
