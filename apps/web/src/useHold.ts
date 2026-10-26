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
  const entering = useRef(false); // the hold is the entry's, not a send's: a picture arriving late that outgrows the view gives it up for the bottom
  const glide = useRef<{ to: number; until: number } | null>(null);

  /** Where the held message sits at the top, and the room that takes under it, from the lines alone (the spacer left out). */
  const place = (box: HTMLElement, room: HTMLElement, el: HTMLElement) => {
    const at = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - PAD;
    const lines = box.scrollHeight - room.offsetHeight; // the content without the spacer, so the room never depends on itself
    return { at, room: Math.ceil(at + box.clientHeight - lines) };
  };

  const toBottom = (box: HTMLElement) => {
    holding.current = false;
    entering.current = false;
    pinned.current = true;
    if (spacer.current) spacer.current.style.height = '0px';
    own.current = true;
    box.scrollTop = box.scrollHeight;
  };

  const hold = (smooth = false, picture = false) => {
    const box = stream.current;
    const room = spacer.current;
    if (!box || !room || !holding.current || held === undefined || anchor.current) return;
    const el = box.querySelector<HTMLElement>(`.msg[data-id="${held}"]`);
    if (!el) return;
    const { at, room: need } = place(box, room, el);
    if (picture && entering.current && need < 0 && !free.current) return toBottom(box);
    room.style.height = `${Math.max(0, need)}px`;
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
  /** A picture arrived: the entry's hold gives way to the bottom if the tail no longer fits under the message. */
  const picture = () => hold(false, true);
  const release = () => {
    holding.current = false;
    if (spacer.current) spacer.current.style.height = '0px';
  };
  /** The person sent: the run's first message goes to the top, gliding. */
  const start = () => {
    entering.current = false;
    holding.current = true;
    free.current = false;
    pinned.current = false;
    hold(true);
  };

  useLayoutEffect(() => {
    if (!ready || entered.current === agent) return;
    entered.current = agent;
    free.current = false;
    // Entering holds the last sent message at the top only when everything after it fits under it; a longer tail would stop the view mid-chat.
    const box = stream.current;
    const el = held === undefined ? null : box?.querySelector<HTMLElement>(`.msg[data-id="${held}"]`);
    holding.current = !!box && !!el && !!spacer.current && place(box, spacer.current, el).room >= 0;
    entering.current = holding.current;
    pinned.current = !holding.current;
    if (holding.current) hold();
    else if (box && el) toBottom(box);
  }, [ready, agent]); // eslint-disable-line react-hooks/exhaustive-deps
  useLayoutEffect(() => hold()); // whatever changed above or below the held message, it stays where it was put
  useEffect(() => {
    const box = stream.current;
    if (!box) return;
    const watch = new ResizeObserver(() => hold());
    watch.observe(box);
    return () => watch.disconnect();
  });
  /** A scroll we did not make (the scrollbar, a key, a drag): the view is the person's. Our own glide reports scrolls too. */
  const scrolled = () => {
    if (holding.current && !free.current && !(glide.current && performance.now() < glide.current.until)) free.current = true;
  };
  return { spacer, hold, picture, release, start, scrolled, holding, free };
}
