import { useEffect, useRef } from 'react';

/** The picked card leaves the list (answered, held): the pick goes to the card that moved up into its place, not to the top one. */
export function useKeepPlace(cards: { key: string }[], pick: string | null, setPick: (key: string | null) => void, on: boolean) {
  const at = useRef(0);
  useEffect(() => {
    const i = cards.findIndex((c) => c.key === pick);
    if (i >= 0 || pick === null || !on) at.current = Math.max(i, 0);
    else setPick(cards[Math.min(at.current, cards.length - 1)]?.key ?? null);
  }, [cards, pick, on, setPick]);
}
