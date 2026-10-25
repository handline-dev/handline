// The chat holds the person's last sent message near the top, like ChatGPT: a send scrolls it there and the reply grows under it, sends before
// a reply stack under the first, and entering the chat shows it there. A spacer after the last line gives the view the room to put it there; its
// size comes from the lines alone, so it never feeds back into itself. The person's own scroll ends the holding of the view, not the spacer's upkeep.
import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

const PAD = 12; // the stream's top padding (pt-3): the held message sits this far below the top
const GLIDE_MS = 600;

export function useHold({
  stream,
  held,
  own,
  anchor,
  pinned,
  ready,
  agent,
}: {
  stream: RefObject<HTMLDivElement | null>;
  /** The id of the message to hold at the top, when the chat has one. */
  held: number | undefined;
  /** Set when a scroll we make is about to be reported, so it is not read as the person's. */
  own: RefObject<boolean>;
  /** A load of older lines is putting the reader's line back: the held message waits. */
  anchor: RefObject<unknown>;
  /** The person is at the bottom: new lines scroll into view. Held while a message is held. */
  pinned: RefObject<boolean>;
  ready: boolean;
  agent: string;
}) {
  const spacer = useRef<HTMLDivElement>(null);
  const holding = useRef(false); // the chat is laid out around a held message
  const free = useRef(false); // the person has scrolled since: the view is theirs
  const entered = useRef<string | null>(null);
  const glide = useRef<{ to: number; until: number } | null>(null);

  const hold = (smooth = false) => {
    const box = stream.current;
    const room = spacer.current;
    if (!box || !room || !holding.current || held === undefined || anchor.current) return;
    const el = box.querySelector<HTMLElement>(`.msg[data-id="${held}"]`);
    if (!el) return;
    const at = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - PAD;
    const lines = box.scrollHeight - room.offsetHeight; // the content without the spacer, so the room never depends on itself
    room.style.height = `${Math.max(0, Math.ceil(at + box.clientHeight - lines))}px`;
    if (free.current) return;
    const going = glide.current;
    if (going && performance.now() < going.until && Math.abs(going.to - at) < 2) return; // a glide to the same place is under way
    glide.current = null;
    if (Math.abs(box.scrollTop - at) < 1) return;
    own.current = true;
    if (smooth && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      glide.current = { to: at, until: performance.now() + GLIDE_MS };
      box.scrollTo({ top: at, behavior: 'smooth' });
    } else box.scrollTop = at;
  };
  const release = () => {
    holding.current = false;
    if (spacer.current) spacer.current.style.height = '0px';
  };
  /** The person sent: the run's first message goes to the top, gliding. */
  const start = () => {
    holding.current = true;
    free.current = false;
    pinned.current = false;
    hold(true);
  };

  useLayoutEffect(() => {
    if (!ready || entered.current === agent) return;
    entered.current = agent;
    free.current = false;
    holding.current = held !== undefined;
    pinned.current = held === undefined;
    hold();
  }, [ready, agent]); // eslint-disable-line react-hooks/exhaustive-deps
  useLayoutEffect(() => hold()); // whatever changed above or below the held message, it stays where it was put
  useEffect(() => {
    const box = stream.current;
    if (!box) return;
    const watch = new ResizeObserver(() => hold());
    watch.observe(box);
    return () => watch.disconnect();
  });
  return { spacer, hold, release, start, holding, free };
}
