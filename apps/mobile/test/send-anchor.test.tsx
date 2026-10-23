import { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import type { ScrollView } from 'react-native';
import { useSendAnchor } from '#src/screens/ChatAnchor.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The hook on a view of 600 high whose scrolling is recorded. */
async function mountAnchor() {
  const scrollTo = vi.fn();
  const scrollToEnd = vi.fn();
  const ref = createRef<ScrollView>() as { current: unknown };
  ref.current = { scrollTo, scrollToEnd };
  let a!: ReturnType<typeof useSendAnchor>;
  function Probe() {
    a = useSendAnchor(ref as never);
    return null;
  }
  await act(async () => createRoot(document.createElement('div')).render(<Probe />));
  const run = (f: () => void) => act(async () => f());
  await run(() => a.layout(600));
  return { a: () => a, run, scrollTo, scrollToEnd };
}
const scrollEvent = (y: number, content: number) =>
  ({ nativeEvent: { contentOffset: { y }, contentSize: { height: content }, layoutMeasurement: { height: 600 } } }) as never;

/** A send at row 900 of a chat 1000 high, once the room under it is laid out (508 with the gap before it). */
async function sent(m: Awaited<ReturnType<typeof mountAnchor>>) {
  await m.run(() => m.a().content(1000));
  m.scrollToEnd.mockClear(); // up to here the chat followed its end
  await m.run(() => (m.a().start(), m.a().row(900)));
  await m.run(() => m.a().content(1508));
}

describe('a sent message goes to the top', () => {
  it('holds the message at the top with room below it, and the reply growing does not move the view', async () => {
    const m = await mountAnchor();
    await m.run(() => m.a().content(1000));
    expect(m.scrollToEnd).toHaveBeenCalled(); // before any send the chat follows its end
    m.scrollToEnd.mockClear();
    await sent(m);
    expect(m.a().spacer).toBe(500); // 100 of the message, 500 of room under it
    expect(m.scrollTo).toHaveBeenLastCalledWith({ y: 892, animated: false });
    await m.run(() => m.a().content(1708)); // the reply grows by 200: the room shrinks
    expect(m.a().spacer).toBe(300);
    await m.run(() => m.a().content(2200)); // taller than the view
    expect(m.a().spacer).toBe(0);
    expect(m.scrollToEnd).not.toHaveBeenCalled();
  });

  it('follows the end again once the person has scrolled to the bottom', async () => {
    const m = await mountAnchor();
    await sent(m);
    await m.run(() => m.a().content(2200)); // the room is used up
    expect(m.a().scrolled(scrollEvent(892, 2200))).toBe(false); // our own jump is not read
    await m.run(() => m.a().drag());
    expect(m.a().scrolled(scrollEvent(1600, 2200))).toBe(true);
    await m.run(() => m.a().content(2400));
    expect(m.scrollToEnd).toHaveBeenCalled();
  });

  it("reading up while the reply grows keeps the reader's place", async () => {
    const m = await mountAnchor();
    await sent(m);
    await m.run(() => m.a().content(2200));
    await m.run(() => m.a().drag());
    expect(m.a().scrolled(scrollEvent(300, 2200))).toBe(false);
    m.scrollTo.mockClear();
    m.scrollToEnd.mockClear();
    await m.run(() => m.a().content(2600));
    expect(m.scrollTo).not.toHaveBeenCalled();
    expect(m.scrollToEnd).not.toHaveBeenCalled();
  });
});
