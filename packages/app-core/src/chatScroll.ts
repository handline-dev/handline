// Where a chat's scroll sits, as one pure model for every platform. An adapter measures the view and feeds events in; the answer says where
// to scroll and how much empty room to leave under the lines. Nothing else moves the scroll.
//
// Three modes: `follow` (at the bottom, new lines scroll into view), `hold` (the person's last sent message sits at the top and the reply
// grows under it: the phone's entry) and `free` (the person is reading; nothing moves, new lines are counted).

export type ChatEntry = 'bottom' | 'lastSent';

export interface ScrollState {
  mode: 'follow' | 'hold' | 'free';
  /** Entries that came while the person read above the bottom. */
  newer: number;
}

/** What the adapter measured, all in the scroll content's coordinates and without the room. */
export interface Geometry {
  /** The height of the lines. */
  lines: number;
  /** The height of the view. */
  view: number;
  /** The top of the person's last sent run, or null when they sent nothing. */
  sent: number | null;
}

export type ScrollEvent =
  /** The first lines are drawn. */
  | { type: 'enter' }
  /** The person sent a message. */
  | { type: 'sent' }
  /** The lines changed size (a late picture, a growing reply, a card opening). */
  | { type: 'grew' }
  /** `count` entries were added at the end. */
  | { type: 'added'; count: number }
  /** The person scrolled; `atBottom` says where they ended. */
  | { type: 'scrolled'; atBottom: boolean }
  /** The person asked to go down (the pill, or the end of a jump). */
  | { type: 'bottom' };

export interface ScrollAnswer {
  state: ScrollState;
  /** Scroll to this offset; null leaves the view where it is. */
  to: number | null;
  /** Glide there instead of jumping. */
  smooth: boolean;
  /** The empty room to keep under the lines so the last send can reach the top, in every mode (the phone's entry; zero on the PC). */
  room: number;
}

/** How long the top of the chat must stay in view, with no page loading, before the next older page is asked for. */
export const OLDER_DWELL_MS = 300;

export const START: ScrollState = { mode: 'follow', newer: 0 };

/** The room a held message at `at` needs under the lines: zero when the lines already reach past the view. */
const roomFor = (g: Geometry, at: number) => Math.max(0, Math.ceil(at + g.view - g.lines));
const end = (g: Geometry) => Math.max(0, g.lines - g.view);

/** One step: the state and the event give the next state and where the view goes. */
export function chatScroll(state: ScrollState, event: ScrollEvent, g: Geometry, entry: ChatEntry): ScrollAnswer {
  // The room is a function of the geometry alone, whatever the mode: the last send can always be scrolled to the top (phone entry only).
  const room = entry === 'lastSent' && g.sent !== null ? roomFor(g, g.sent) : 0;
  const stay = (s: ScrollState): ScrollAnswer => ({ state: s, to: null, smooth: false, room });
  const follow = (smooth: boolean): ScrollAnswer => ({ state: START, to: end(g), smooth, room });
  const hold = (smooth: boolean): ScrollAnswer =>
    g.sent === null ? follow(smooth) : { state: { mode: 'hold', newer: 0 }, to: g.sent, smooth, room };
  switch (event.type) {
    case 'enter':
      // The held entry only when everything after the message fits under it; a longer tail would stop the view mid-chat.
      return entry === 'lastSent' && g.sent !== null && g.lines - g.sent <= g.view ? hold(false) : follow(false);
    case 'sent':
      return entry === 'lastSent' ? hold(true) : follow(true);
    case 'bottom':
      return follow(true);
    case 'grew':
      if (state.mode === 'follow') return follow(false);
      // The held message stays where it was put; only the room under it changes.
      if (state.mode === 'hold' && g.sent !== null) return { state, to: g.sent, smooth: false, room };
      return stay(state);
    case 'added':
      return stay(state.mode === 'free' ? { ...state, newer: state.newer + event.count } : state);
    case 'scrolled':
      // A scroll the adapter did not make is the person's: the view is theirs until they are back at the bottom.
      return stay(event.atBottom ? START : { mode: 'free', newer: state.mode === 'free' ? state.newer : 0 });
  }
}
