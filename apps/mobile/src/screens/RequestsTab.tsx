// The Requests tab: what waits on the person in the PC's four groups (Allow, Decide, Review, Do by hand), each row with its title, where it came from
// and how long it has waited. A row opens its item full screen; the list is the one the item's "n of N" walks.
import { FlatList, Pressable, Text, View } from 'react-native';
import { REQUEST_GROUPS, ago, requestGroup, sourceWord, type Card, type LaterAt } from '@handline/app-core';
import { useInbox, useMyTurn, useServes } from '@handline/app-data';
import { font, space } from '@handline/tokens';
import { usePalette } from '#src/theme.ts';
import type { Held } from '#src/heldAnswers.ts';
import { Loadable } from '#src/screens/Loadable.tsx';
import { PageHeader } from '#src/screens/parts.tsx';
import { RequestItem } from '#src/screens/RequestItem.tsx';
import { LaterFold, type Deferred } from '#src/screens/RequestsLater.tsx';

/** The cards that wait now: what a held answer took off the list is not among them, and none is counted twice. */
export function useRequestCards(held: Held): Card[] {
  return useMyTurn().cards.filter((c) => !held.away.includes(c.key));
}

/** The items put off to Later, with when each returns; one a held answer took away is not among them. */
export function useRequestLater(held: Held): Deferred[] {
  return useMyTurn().later.filter((l) => !held.away.includes(l.card.key));
}

/** The cards in the order the list shows them: what runs out first, then the rest group by group. */
const inOrder = (cards: Card[]) => {
  const timed = cards.filter((c) => c.due != null).sort((a, b) => a.due! - b.due!);
  return [...timed, ...REQUEST_GROUPS.flatMap((g) => cards.filter((c) => c.due == null && requestGroup(c) === g))];
};

export function RequestsTab(props: {
  held: Held;
  /** The open item's key, or null for the list. */
  item: string | null;
  onItem: (key: string | null) => void;
  /** What is put off, and the call that puts an item off (a time) or returns it (null). */
  later: Deferred[];
  onLater: (card: Card, at: LaterAt | null) => void;
  onTask: (id: number) => void;
}) {
  const cards = useRequestCards(props.held);
  const query = useInbox();
  const { served } = useServes('inbox.list');
  const ordered = inOrder(cards);
  const at = ordered.findIndex((x) => x.key === props.item);
  const off = props.later.find((l) => l.card.key === props.item);
  const card = at >= 0 ? ordered[at] : off?.card;
  if (card)
    return (
      <RequestItem
        key={card.key}
        card={card}
        at={at >= 0 ? { n: at + 1, of: ordered.length } : null}
        later={off?.at ?? null}
        onLater={props.onLater}
        held={props.held}
        onBack={() => props.onItem(null)}
        onStep={(by) => props.onItem(ordered[at + by]?.key ?? props.item)}
        // The held card is already off the list when this runs, so the next one stands where this one stood.
        onAnswered={() => props.onItem(ordered[at + 1]?.key ?? null)}
        onTask={props.onTask}
      />
    );
  return (
    <View style={{ flex: 1 }}>
      <PageHeader title="Requests" />
      <Loadable
        query={query}
        served={served}
        isEmpty={cards.length === 0 && props.later.length === 0}
        skeleton="inbox"
        label="Requests"
        empty="Nothing needs you."
      >
        <FlatList
          ListFooterComponent={<LaterFold items={props.later} onOpen={props.onItem} />}
          data={ordered}
          keyExtractor={(x) => x.key}
          renderItem={({ item: x }) => <Row card={x} onPress={() => props.onItem(x.key)} />}
        />
      </Loadable>
    </View>
  );
}

function Row({ card, onPress }: { card: Card; onPress: () => void }) {
  const c = usePalette();
  const meta = [sourceWord(card), card.from, card.since ? ago(new Date(card.since).toISOString()) : ''].filter(Boolean).join(' · ');
  return (
    <Pressable
      testID={`request-${card.key}`}
      accessibilityRole="button"
      accessibilityLabel={`${requestGroup(card)}: ${card.title}`}
      onPress={onPress}
      style={{ borderTopWidth: 1, borderTopColor: c.line, padding: space.lg, minHeight: 56, gap: space.xs }}
    >
      <Text numberOfLines={2} style={{ color: c.ink, fontSize: font.size.md }}>
        {card.title}
      </Text>
      <Text numberOfLines={1} style={{ color: c.muted, fontSize: font.size.sm }}>
        {meta}
      </Text>
    </Pressable>
  );
}
