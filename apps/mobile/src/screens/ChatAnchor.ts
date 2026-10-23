// Like ChatGPT: a message the person sends scrolls to the top of the chat and the reply grows under it. An empty spacer after the last line
// gives the view room to put it there; the view then stays where it is until the person scrolls themselves.
import { useRef, useState } from 'react';
import type { NativeScrollEvent, NativeSyntheticEvent, ScrollView } from 'react-native';
import { space } from '@handline/tokens';

const NEAR_BOTTOM = 40;

export function useSendAnchor(scroll: React.RefObject<ScrollView | null>) {
  const [spacer, setSpacer] = useState(0);
  const gap = useRef(0); // the spacer's own height as laid out, with the gap before it
  const anchored = useRef(false); // the last send is held at the top
  const dragged = useRef(false); // the person has moved the view since
  const top = useRef<number | null>(null); // where the sent message's row starts
  const size = useRef({ view: 0, content: 0 });
  const follow = useRef(true); // new entries scroll into view: the person is at the bottom

  const fit = () => {
    if (!anchored.current || top.current === null) return;
    const real = size.current.content - gap.current;
    const next = Math.max(0, Math.round(size.current.view - (real - top.current)));
    gap.current = next ? next + space.sm : 0;
    setSpacer(next);
    if (!dragged.current) scroll.current?.scrollTo({ y: Math.max(0, top.current - space.sm), animated: false });
  };

  return {
    spacer,
    /** The person sent: hold the message at the top from now on. */
    start() {
      anchored.current = true;
      dragged.current = false;
      follow.current = false;
      top.current = null;
    },
    /** The view stays where it is: a search hit, or the person leaving it. */
    release() {
      anchored.current = false;
      follow.current = false;
    },
    /** The row of the person's newest message was laid out at `y`. */
    row(y: number) {
      if (!anchored.current || dragged.current) return;
      top.current = y;
      fit();
    },
    layout: (view: number) => void (size.current.view = view),
    content(h: number) {
      size.current.content = h;
      if (follow.current) scroll.current?.scrollToEnd({ animated: false });
      else if (anchored.current) fit();
    },
    drag: () => void (dragged.current = true),
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
