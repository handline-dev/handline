// Search box above the agent's Chat: chat.search in the scope the tab shows; a hit goes to its message through the tab's jump (chat.locate).
// A daemon that does not list chat.search shows no box.
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native';
import { classify, sentence, useChatSearch, useServes, type ChatScope } from '@handline/app-data';
import { radius, space } from '@handline/tokens';
import { usePalette } from '../theme.ts';

const SNIPPET = 90;

/** The words around the first match: ‘…’ marks a cut end. */
function snippet(body: string, query: string): string {
  const flat = body.replace(/\s+/g, ' ').trim();
  const from = Math.max(0, flat.toLowerCase().indexOf(query.toLowerCase()) - 30);
  return `${from > 0 ? '…' : ''}${flat.slice(from, from + SNIPPET)}${from + SNIPPET < flat.length ? '…' : ''}`;
}

const when = (at: string) => {
  const d = new Date(at);
  return Number.isNaN(+d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
};

export function ChatSearch(props: { agent: string; scope: ChatScope; onGo: (id: number) => void }) {
  const c = usePalette();
  const { served } = useServes('chat.search');
  const [text, setText] = useState('');
  const [query, setQuery] = useState(''); // the text once the person stops typing
  useEffect(() => {
    const t = setTimeout(() => setQuery(text.trim()), 250);
    return () => clearTimeout(t);
  }, [text]);
  const found = useChatSearch({ agent: props.agent, query, scope: props.scope }, served && query !== '');
  if (!served) return null;
  const hits = found.data?.messages ?? [];
  // Results show for the text as it stands, not for the query of a moment ago.
  const showing = text.trim() !== '' && query === text.trim();
  return (
    <View style={{ paddingHorizontal: space.md, paddingBottom: space.sm, gap: space.xs }}>
      <TextInput
        testID="chat-search"
        value={text}
        onChangeText={setText}
        placeholder="Search this chat"
        placeholderTextColor={c.muted}
        accessibilityLabel="Search messages"
        returnKeyType="search"
        autoCapitalize="none"
        autoCorrect={false}
        style={{ color: c.ink, borderColor: c.line, borderWidth: 1, borderRadius: radius.md, padding: space.sm }}
      />
      {showing && (
        <ScrollView testID="search-results" style={{ maxHeight: 280 }} keyboardShouldPersistTaps="handled">
          {found.isError ? (
            <Text accessibilityRole="alert" style={{ color: c.block }}>
              {sentence(classify(found.error)) ?? 'Search failed'}
            </Text>
          ) : found.data && !hits.length ? (
            <Text testID="search-empty" style={{ color: c.muted, padding: space.sm }}>
              No messages found. Nothing in this chat says “{query}”.
            </Text>
          ) : (
            hits.map((m) => (
              <Pressable
                key={m.id}
                testID={`hit-${m.id}`}
                accessibilityRole="button"
                onPress={() => {
                  setText('');
                  props.onGo(m.id);
                }}
                style={{ paddingVertical: space.sm, gap: 2, borderBottomWidth: 1, borderBottomColor: c.line }}
              >
                <Text style={{ color: c.muted, fontSize: 11 }}>
                  {m.agent ?? props.agent} · {m.from} → {m.to} · {when(m.at)}
                </Text>
                <Text style={{ color: c.ink }}>{snippet(m.body, query)}</Text>
              </Pressable>
            ))
          )}
          {found.isFetching && <Text style={{ color: c.muted, fontSize: 12 }}>Searching…</Text>}
        </ScrollView>
      )}
    </View>
  );
}
