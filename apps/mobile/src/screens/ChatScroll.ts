// The phone's side of the chat's scroll: it measures the ScrollView and feeds events to the shared `chatScroll`, then does what it answers.
// The person's last sent message goes to the top and the reply grows under it ('lastSent'); the room it needs is an empty spacer after the lines.
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
  const laid = useRef(0); // the room as laid out, with the gap before it
  const size = useRef({ view: 0, content: 0 });
  const sentY = useRef<number | null>(null); // where the sent row starts
  const entered = useRef(false);
  const sending = useRef(false); // a send whose row has not been laid out yet
  const ours = useRef(false); // the scrolling so far is ours: the place is not read from it until the person drags

  const geometry = () => ({ lines: Math.max(0, size.current.content - laid.current), view: size.current.view, sent: sentY.current });

  const feed = (e: ScrollEvent) => {
    const a = chatScroll(state.current, e, geometry(), 'lastSent');
    state.current = a.state;
    // A reader who left the held place keeps the room under them: it only goes when the view is back at the bottom.
    if (a.state.mode === 'hold' || a.state.mode === 'follow') {
      laid.current = a.room ? a.room + space.sm : 0;
      setRoom(a.room);
    }
    if (a.to !== null) {
      ours.current = true;
      scroll.current?.scrollTo({ y: a.to, animated: a.smooth });
    }
  };

  /** The first lines count once they are drawn and measured, with the sent row among them. */
  const settle = () => {
    const { view, content } = size.current;
    if (entered.current || !now.current.ready || !view || !content) return;
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
      sentY.current = Math.max(0, y - space.sm);
      if (sending.current) {
        sending.current = false;
        feed({ type: 'sent' });
      } else if (entered.current) feed({ type: 'grew' });
      else settle();
    },
    layout(view: number) {
      size.current.view = view;
      settle();
    },
    content(h: number) {
      size.current.content = h;
      if (entered.current) feed({ type: 'grew' });
      else settle();
    },
    drag: () => void (ours.current = false),
    /** True while the view follows the end of the chat. */
    scrolled(e: NativeSyntheticEvent<NativeScrollEvent>) {
      if (ours.current) return state.current.mode === 'follow';
      const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
      // The room is empty: being at its end is not being at the bottom of the conversation.
      const atBottom = contentSize.height - laid.current - contentOffset.y - layoutMeasurement.height < NEAR_BOTTOM;
      feed({ type: 'scrolled', atBottom });
      return state.current.mode === 'follow';
    },
  };
}
