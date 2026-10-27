// The phone's side of the chat's scroll: it measures the ScrollView and feeds events to the shared `chatScroll`, then does what it answers.
// The person's last sent message goes to the top and the reply grows under it ('lastSent'); the room it needs is an empty spacer after the lines.
// The room depends on the geometry alone and is refitted whenever the view or the lines change, so the last send can be scrolled to the top again
// after the reader has been away. The lines are measured on their own wrapper, never as the content minus the room, which a late report would skew.
import { useEffect, useRef, useState } from 'react';
import type { NativeScrollEvent, NativeSyntheticEvent, ScrollView } from 'react-native';
import { chatScroll, START, type ScrollEvent, type ScrollState } from '@handline/app-core';
import { space } from '@handline/tokens';

const NEAR_BOTTOM = 40;

/** `ready`: the lines are read and drawn. `sent`: the id of the row the person's last send is on, when there is one. */
export function useChatScroll(scroll: React.RefObject<ScrollView | null>, o: { ready: boolean; sent: number | undefined }) {
  const [room, setRoom] = useState(0);
  const now = useRef(o);
  now.current = o;
  const state = useRef<ScrollState>(START);
  const size = useRef({ view: 0, lines: 0 }); // the lines include the padding around them
  const sentY = useRef<number | null>(null); // where the sent row starts
  const entered = useRef(false);
  const sending = useRef(false); // a send whose row has not been laid out yet
  const ours = useRef(false); // the scrolling so far is ours: the place is not read from it until the person drags

  const geometry = () => ({ lines: size.current.lines, view: size.current.view, sent: sentY.current });

  const feed = (e: ScrollEvent) => {
    const a = chatScroll(state.current, e, geometry(), 'lastSent');
    state.current = a.state;
    setRoom(a.room);
    if (a.to !== null) {
      ours.current = true;
      scroll.current?.scrollTo({ y: a.to, animated: a.smooth });
    }
  };

  /** The first lines count once they are drawn and measured, with the sent row among them. */
  const settle = () => {
    const { view, lines } = size.current;
    if (entered.current || !now.current.ready || !view || !lines) return;
    if (now.current.sent !== undefined && sentY.current === null) return;
    entered.current = true;
    feed({ type: 'enter' });
  };

  const later = useRef(settle); // the render's own settle, for the effect that waits on `ready`
  later.current = settle;
  useEffect(() => later.current(), [o.ready]);

  return {
    room,
    /** The person sent: the message goes to the top once its row is laid out. */
    start() {
      sending.current = true;
    },
    /** The view stays where it is: a search hit, or the person leaving it. */
    release() {
      ours.current = true;
      feed({ type: 'scrolled', atBottom: false });
    },
    /** The row `id` was laid out at `y`. */
    row(id: number, y: number) {
      if (id !== now.current.sent) return;
      sentY.current = Math.max(0, y); // y is in the lines' own coordinates
      if (sending.current) {
        sending.current = false;
        feed({ type: 'sent' });
      } else if (entered.current) feed({ type: 'grew' });
      else settle();
    },
    /** The view changed size: the keyboard, the composer, a turn of the phone. */
    layout(view: number) {
      size.current.view = view;
      if (entered.current) feed({ type: 'grew' });
      else settle();
    },
    /** The lines (without the room) are `h` high. */
    lines(h: number) {
      size.current.lines = h + 2 * space.sm;
      if (entered.current) feed({ type: 'grew' });
      else settle();
    },
    drag: () => void (ours.current = false),
    /** True while the view follows the end of the chat. */
    scrolled(e: NativeSyntheticEvent<NativeScrollEvent>) {
      if (ours.current) return state.current.mode === 'follow';
      const { contentOffset, layoutMeasurement } = e.nativeEvent;
      // The room is empty: being at its end is not being at the bottom of the conversation.
      const atBottom = size.current.lines - contentOffset.y - layoutMeasurement.height < NEAR_BOTTOM;
      feed({ type: 'scrolled', atBottom });
      return state.current.mode === 'follow';
    },
  };
}
