import { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import type { ScrollView } from 'react-native';
import { space } from '@handline/tokens';
import { useChatScroll } from '#src/screens/ChatScroll.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const VIEW = 600;
const SENT = 42; // the id of the row of the person's last send

/** The phone's scroll adapter on a view of 600 high whose scrolling is recorded; `props` are what the chat screen hands it. */
async function mount(props: { ready: boolean; sent: number | undefined }) {
  const scrollTo = vi.fn();
  const ref = createRef<ScrollView>() as { current: unknown };
  ref.current = { scrollTo };
  let a!: ReturnType<typeof useChatScroll>;
  let now = props;
  function Probe() {
    a = useChatScroll(ref as never, now);
    return null;
  }
  const root = createRoot(document.createElement('div'));
  await act(async () => root.render(<Probe />));
  return {
    a: () => a,
    scrollTo,
    run: (f: () => void) => act(async () => f()),
    props: (next: typeof props) => act(async () => ((now = next), root.render(<Probe />))),
  };
}
type M = Awaited<ReturnType<typeof mount>>;

/** The lines are `n` high with the padding around them; the phone reports the wrapper, which is that minus the padding. */
const lines = (m: M, n: number) => m.run(() => m.a().lines(n - 2 * space.sm));
/** The row of the last send starts at `y` in the lines' coordinates with the padding above it. */
const rowAt = (m: M, y: number) => m.run(() => m.a().row(SENT, y - space.sm));
const scrollEvent = (y: number) => ({ nativeEvent: { contentOffset: { y }, layoutMeasurement: { height: VIEW } } }) as never;
const last = (m: M) => m.scrollTo.mock.calls.at(-1)?.[0];

/** A chat 1000 high whose last send starts at row 900: its tail (100) fits under it in a view of 600. */
async function entered(m: M, sentAt = 900, content = 1000) {
  await m.run(() => m.a().layout(VIEW));
  await rowAt(m, sentAt);
  await lines(m, content);
}

describe('the chat scroll on the phone', () => {
  it('enters with the last send at the top and room under it when its tail fits', async () => {
    const m = await mount({ ready: true, sent: SENT });
    await entered(m);
    expect(last(m)).toEqual({ y: 900 - space.sm, animated: false });
    expect(m.a().room).toBe(900 - space.sm + VIEW - 1000);
  });

  it('enters at the bottom when the tail does not fit under the last send', async () => {
    const m = await mount({ ready: true, sent: SENT });
    await entered(m, 300);
    expect(last(m)).toEqual({ y: 1000 - VIEW, animated: false });
    expect(m.a().room).toBe(0);
  });

  it('waits for the lines to be read before it enters', async () => {
    const m = await mount({ ready: false, sent: SENT });
    await entered(m);
    expect(m.scrollTo).not.toHaveBeenCalled();
    await m.props({ ready: true, sent: SENT });
    expect(last(m)).toEqual({ y: 900 - space.sm, animated: false });
  });

  it('a send glides to the top once its row is laid out, and the reply growing does not move the view', async () => {
    const m = await mount({ ready: true, sent: undefined });
    await m.run(() => m.a().layout(VIEW));
    await lines(m, 500); // entered at the bottom of a short chat
    m.scrollTo.mockClear();
    await m.run(() => m.a().start());
    expect(m.scrollTo).not.toHaveBeenCalled(); // the row is not there yet
    await m.props({ ready: true, sent: SENT });
    await lines(m, 608); // the send's row is in the lines: 100 high, starting at 508
    await rowAt(m, 508);
    expect(last(m)).toEqual({ y: 500, animated: true });
    expect(m.a().room).toBe(492); // the room it needs to reach the top of a view of 600
    await lines(m, 808); // the reply grows by 200: the room shrinks (the lines do not include the room)
    expect(m.a().room).toBe(292);
    expect(last(m)).toEqual({ y: 500, animated: false });
  });

  it('follows the end again once the person has scrolled to the bottom', async () => {
    const m = await mount({ ready: true, sent: SENT });
    await entered(m, 300);
    await lines(m, 1200);
    expect(last(m)).toEqual({ y: 1200 - VIEW, animated: false }); // a followed chat keeps its end
    expect(m.a().scrolled(scrollEvent(500))).toBe(true); // our own scrolling is not read as the person's
    await m.run(() => m.a().drag());
    expect(m.a().scrolled(scrollEvent(100))).toBe(false); // they read above the end
    m.scrollTo.mockClear();
    await lines(m, 1400);
    expect(m.scrollTo).not.toHaveBeenCalled();
    expect(m.a().scrolled(scrollEvent(800))).toBe(true); // and are back at the bottom
    await lines(m, 1600);
    expect(last(m)).toEqual({ y: 1600 - VIEW, animated: false });
  });

  it("reading up from the held place keeps the reader's place while the room follows the lines", async () => {
    const m = await mount({ ready: true, sent: SENT });
    await entered(m);
    await m.run(() => m.a().drag());
    expect(m.a().scrolled(scrollEvent(300))).toBe(false);
    m.scrollTo.mockClear();
    await lines(m, 1300);
    expect(m.scrollTo).not.toHaveBeenCalled();
    expect(m.a().room).toBe(900 - space.sm + VIEW - 1300); // the lines grew by 300, so the room under them is 300 less
  });

  it('keeps the room when the reader comes back to the bottom of the lines, so the last send can sit at the top again', async () => {
    const m = await mount({ ready: true, sent: SENT });
    await entered(m);
    await m.run(() => m.a().drag());
    expect(m.a().scrolled(scrollEvent(100))).toBe(false); // up in the history
    expect(m.a().room).toBe(room);
    expect(m.a().scrolled(scrollEvent(1000 - VIEW))).toBe(true); // and down at the end of the lines
    expect(m.a().room).toBe(room);
  });

  it('refits the room when the view changes size (the keyboard, the composer, a turn of the phone)', async () => {
    const m = await mount({ ready: true, sent: SENT });
    await entered(m);
    await m.run(() => m.a().layout(500));
    expect(m.a().room).toBe(900 - space.sm + 500 - 1000);
    await m.run(() => m.a().layout(900));
    expect(m.a().room).toBe(900 - space.sm + 900 - 1000);
  });

  it('a search hit leaves the view where the hit put it', async () => {
    const m = await mount({ ready: true, sent: SENT });
    await entered(m);
    await m.run(() => m.a().release());
    m.scrollTo.mockClear();
    await lines(m, 1400);
    expect(m.scrollTo).not.toHaveBeenCalled();
  });
});
