// Like ChatGPT: a message the person sends scrolls to the top of the chat and the reply grows under it. Messages sent before the reply
// stack under the first of them, which stays at the top; on entry the chat opens with the person's last message there (or at its end when
// there is none), laid out out of sight and shown once placed, so it never moves in front of the person. An empty spacer after the last line
// gives the view room to put it there; the view then stays where it is until the person scrolls themselves. The spacer is worked out from the
// lines' own height, never from the scroll content that already holds it, so a resize or a late layout cannot make it flip between two sizes.
import { useRef, useState } from 'react';
import type { NativeScrollEvent, NativeSyntheticEvent, ScrollView } from 'react-native';
import { space } from '@handline/tokens';

const NEAR_BOTTOM = 40;
const PAD = space.sm; // the scroll content's padding, and its gap before the spacer

/** The entry held at the top: the first of the person's last run of messages, so ones sent before a reply stack under it and a reply lands below it. */
export function heldId<T extends { id: number }>(entries: T[], mine: (e: T) => boolean): number | undefined {
  let at = entries.findLastIndex(mine);
  while (at > 0 && mine(entries[at - 1]!)) at--;
  return entries[at]?.id;
}

export function useSendAnchor(scroll: React.RefObject<ScrollView | null>) {
  const [spacer, setSpacer] = useState(0);
  const [placed, setPlaced] = useState(false); // the entry position is made: until then the lines are hidden
  const entered = useRef(false);
  const seen = useRef<number | null>(null); // where the held row was laid out, which can come before the chat is entered
  const anchored = useRef(false); // the last send is held at the top
  const dragged = useRef(false); // a finger has been on the view since the send
  const top = useRef<number | null>(null); // where the view must scroll to for the sent message to sit at the top
  const size = useRef({ view: 0, lines: 0, content: 0 });
  const follow = useRef(true); // new entries scroll into view: the person is at the bottom
  const due = useRef<'glide' | 'snap' | null>(null); // a scroll to the top still to make once the room for it is laid out

  /** The content height the scroll view will have once `room` of spacer is laid out. */
  const heightWith = (room: number) => 2 * PAD + size.current.lines + (room ? PAD + room : 0);

  const settle = () => {
    if (due.current === null || top.current === null || dragged.current) return;
    if (size.current.content + 1 < top.current + size.current.view) return; // the room is not laid out yet (a pixel of rounding is not a lack); the content size will say when
    scroll.current?.scrollTo({ y: top.current, animated: due.current === 'glide' });
    due.current = null;
    setPlaced(true);
  };

  const fit = () => {
    if (!anchored.current || top.current === null) return;
    const next = Math.max(0, Math.ceil(top.current + size.current.view - heightWith(0) - PAD));
    setSpacer(next);
    if (heightWith(next) === size.current.content) settle();
  };

  return {
    spacer,
    placed,
    /** The chat has loaded: hold the person's last message at the top, or follow the end when there is none. */
    enter(held: boolean) {
      if (entered.current) return;
      entered.current = true;
      if (!held) return;
      anchored.current = true;
      follow.current = false;
      due.current = 'snap';
      top.current = seen.current;
      fit();
    },
    /** The person sent: hold the message at the top from now on. `stacked`: it joins messages still unanswered, so the top stays where it is. */
    start(stacked = false) {
      anchored.current = true;
      dragged.current = false;
      follow.current = false;
      if (!stacked) top.current = null;
      due.current = 'glide';
      if (stacked) fit();
    },
    /** The view stays where it is: a search hit, or the person leaving it. */
    release() {
      anchored.current = false;
      follow.current = false;
      due.current = null;
    },
    /** The row of the person's newest message was laid out at `y`, in the lines. */
    row(y: number) {
      seen.current = y;
      if (!anchored.current || dragged.current) return;
      top.current = Math.max(0, y);
      fit();
    },
    layout(view: number) {
      const changed = size.current.view !== view;
      size.current.view = view;
      if (changed && anchored.current && !dragged.current && due.current === null) due.current = 'snap'; // a keyboard came or went: put the message back
      fit();
    },
    /** The lines (everything but the spacer) were laid out `h` high. */
    lines(h: number) {
      size.current.lines = h;
      fit();
    },
    content(h: number) {
      size.current.content = h;
      if (!entered.current) return;
      if (follow.current) {
        scroll.current?.scrollToEnd({ animated: false });
        setPlaced(true);
      } else settle();
    },
    /** A finger is on the view: whatever it does, the view is theirs. */
    hold() {
      dragged.current = true;
      due.current = null;
    },
    scrolled(e: NativeSyntheticEvent<NativeScrollEvent>) {
      // Our own jump to the top is not the person's: until they move the view, the place is not read from it.
      if (anchored.current && !dragged.current) return follow.current;
      const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
      // The spacer is empty room: being at its end is not being at the bottom of the conversation.
      follow.current = !spacer && contentSize.height - contentOffset.y - layoutMeasurement.height < NEAR_BOTTOM;
      return follow.current;
    },
  };
}
