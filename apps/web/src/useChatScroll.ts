// The web side of the chat's scroll: measures the view, feeds the events to the shared `chatScroll` and does what it answers. This is the only
// place that sets the stream's scroll position, and the PC enters a chat at the bottom and never pins a sent message to the top.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { START, chatScroll, type ChatEntry, type Geometry, type ScrollEvent, type ScrollState } from '@handline/app-core';

const NEAR = 40; // px from the end that still counts as the bottom
const GLIDE_MS = 600;

/** The first line in view and how far below the top of `box` it sits: what a reader is looking at. */
function lineInView(box: HTMLElement): { id: string; off: number } | null {
  const { top } = box.getBoundingClientRect();
  for (const el of box.querySelectorAll('[data-id]')) {
    const at = el.getBoundingClientRect();
    const id = el.getAttribute('data-id');
    if (id && at.bottom > top) return { id, off: at.top - top };
  }
  return null;
}

/** Scrolls `box` so the line `a` remembered is back where it was. Found again by its id: a page landing can re-create the element. */
function putBack(box: HTMLElement, a: { id: string; off: number }) {
  const el = [...box.querySelectorAll('[data-id]')].find((n) => n.getAttribute('data-id') === a.id);
  if (!el) return;
  const drift = el.getBoundingClientRect().top - box.getBoundingClientRect().top - a.off;
  if (drift) box.scrollTop += drift;
}

/** Moves `box` to `top`; the DOM write lives here, outside the hook, so the compiler sees no mutation of the ref's element. */
function scrollTo(box: HTMLElement, top: number, smooth: boolean) {
  if (smooth) box.scrollTo({ top, behavior: 'smooth' });
  else box.scrollTop = top;
}

export function useChatScroll({
  stream,
  content,
  ready,
  agent,
  entry = 'bottom',
  reading,
}: {
  /** The scrolling box. */
  stream: RefObject<HTMLDivElement | null>;
  /** Everything inside it: its size changes are what move the lines. */
  content: RefObject<HTMLDivElement | null>;
  /** The lines are drawn: the entry lands now. */
  ready: boolean;
  agent: string;
  entry?: ChatEntry;
  /** Told whether the reader is above the bottom, so a reconnect keeps the older pages they read. */
  reading?: (above: boolean) => void;
}) {
  const state = useRef<ScrollState>(START);
  const [newer, setNewer] = useState(0);
  const entered = useRef<string | null>(null);
  const glide = useRef(0); // until when our own smooth scroll is under way
  const intent = useRef(false); // the person is driving the scroll (wheel, touch, key, scrollbar)
  const place = useRef<{ id: string; off: number } | null>(null); // the line a reader above the bottom is on

  const geometry = (box: HTMLElement): Geometry => ({ lines: box.scrollHeight, view: box.clientHeight, sent: null });

  const dispatch = useCallback(
    (event: ScrollEvent) => {
      const box = stream.current;
      if (!box) return;
      const a = chatScroll(state.current, event, geometry(box), entry);
      state.current = a.state;
      setNewer(a.state.newer);
      if (a.to === null) return;
      if (a.smooth && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        glide.current = performance.now() + GLIDE_MS;
        intent.current = false;
        scrollTo(box, a.to, true);
      } else {
        glide.current = 0;
        scrollTo(box, a.to, false);
      }
    },
    [stream, entry],
  );

  // Entering: once per chat, when its lines are first drawn.
  useLayoutEffect(() => {
    if (!ready || entered.current === agent) return;
    entered.current = agent;
    state.current = START;
    place.current = null;
    dispatch({ type: 'enter' });
  }, [ready, agent, dispatch]);

  // A commit that changes the lines above the reader (a step folding, a page landing, a card coming) leaves them on the line they were on.
  useLayoutEffect(() => {
    const box = stream.current;
    if (box && state.current.mode === 'free' && place.current) putBack(box, place.current);
  });

  // Whatever grows the lines moves nothing the reader is looking at: the bottom stays the bottom, a line above it stays under the reader.
  useEffect(() => {
    const box = stream.current;
    const inner = content.current;
    if (!box || !inner) return;
    const watch = new ResizeObserver(() => {
      if (state.current.mode === 'free') {
        if (place.current) putBack(box, place.current);
      } else dispatch({ type: 'grew' });
    });
    watch.observe(box);
    watch.observe(inner);
    return () => watch.disconnect();
  }, [stream, content, dispatch]);

  return {
    newer,
    /** The next scroll event is the person's if one of these came first. */
    driven: () => void (intent.current = true),
    onScroll: () => {
      const box = stream.current;
      if (!box) return;
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < NEAR;
      // Our own glide reports scrolls on its way down: they are not the person leaving the bottom.
      if (!atBottom && !intent.current && performance.now() < glide.current) return;
      dispatch({ type: 'scrolled', atBottom });
      place.current = atBottom ? null : lineInView(box);
      reading?.(!atBottom);
    },
    /** Entries were added at the end. */
    added: (count: number) => dispatch({ type: 'added', count }),
    /** The person sent. */
    sent: () => dispatch({ type: 'sent' }),
    /** Down to the bottom: the pill, or the way back from a window around an old message. */
    bottom: () => dispatch({ type: 'bottom' }),
    /** A jump to an old message is leaving the bottom: the view is the reader's from here. */
    leave: () => {
      state.current = { mode: 'free', newer: 0 };
      place.current = null;
    },
  };
}
