// Like ChatGPT: a message the person sends scrolls to the top of the chat and the reply grows under it. An empty spacer after the last line
// gives the view room to put it there; the view then stays where it is until the person scrolls themselves. The spacer is worked out from the
// lines' own height, never from the scroll content that already holds it, so a resize or a late layout cannot make it flip between two sizes.
import { useRef, useState } from 'react';
import type { NativeScrollEvent, NativeSyntheticEvent, ScrollView } from 'react-native';
import { space } from '@handline/tokens';

const NEAR_BOTTOM = 40;
const PAD = space.sm; // the scroll content's padding, and its gap before the spacer

export function useSendAnchor(scroll: React.RefObject<ScrollView | null>) {
  const [spacer, setSpacer] = useState(0);
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
    if (size.current.content < top.current + size.current.view) return; // the room is not laid out yet; the content size will say when
    scroll.current?.scrollTo({ y: top.current, animated: due.current === 'glide' });
    due.current = null;
  };

  const fit = () => {
    if (!anchored.current || top.current === null) return;
    const next = Math.max(0, Math.round(top.current + size.current.view - heightWith(0) - PAD));
    setSpacer(next);
    if (heightWith(next) === size.current.content) settle();
  };

  return {
    spacer,
    /** The person sent: hold the message at the top from now on. */
    start() {
      anchored.current = true;
      dragged.current = false;
      follow.current = false;
      top.current = null;
      due.current = 'glide';
    },
    /** The view stays where it is: a search hit, or the person leaving it. */
    release() {
      anchored.current = false;
      follow.current = false;
      due.current = null;
    },
    /** The row of the person's newest message was laid out at `y`, in the lines. */
    row(y: number) {
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
      if (follow.current) scroll.current?.scrollToEnd({ animated: false });
      else settle();
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
