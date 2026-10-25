import { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import type { ScrollView } from 'react-native';
import { heldId, useSendAnchor } from '#src/screens/ChatAnchor.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The hook on a view of 600 high whose scrolling is recorded. */
async function mountAnchor(enter = true) {
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
  await run(() => (a.layout(600), enter && a.enter(false)));
  return { a: () => a, run, scrollTo, scrollToEnd };
}
const scrollEvent = (y: number, content: number) =>
  ({ nativeEvent: { contentOffset: { y }, contentSize: { height: content }, layoutMeasurement: { height: 600 } } }) as never;

/** A send at row 900 of lines 1000 high (the content is 1016 with its padding), once the room under it is laid out. */
async function sent(m: Awaited<ReturnType<typeof mountAnchor>>) {
  await m.run(() => (m.a().lines(1000), m.a().content(1016)));
  m.scrollToEnd.mockClear(); // up to here the chat followed its end
  await m.run(() => (m.a().start(), m.a().row(900)));
  // 900 + 600 of view - 24 of padding and gap - 1000 of lines: 476 of room, 1016 + 8 + 476 laid out
  await m.run(() => m.a().content(1500));
}

describe('a sent message goes to the top', () => {
  it('glides to the message once the room is laid out, and the reply growing does not move the view', async () => {
    const m = await mountAnchor();
    await m.run(() => (m.a().lines(1000), m.a().content(1016)));
    expect(m.scrollToEnd).toHaveBeenCalled(); // before any send the chat follows its end
    m.scrollToEnd.mockClear();
    await m.run(() => (m.a().start(), m.a().row(900)));
    expect(m.a().spacer).toBe(476);
    expect(m.scrollTo).not.toHaveBeenCalled(); // the room is not there yet: a scroll now would stop short
    await m.run(() => m.a().content(1500));
    expect(m.scrollTo).toHaveBeenCalledTimes(1);
    expect(m.scrollTo).toHaveBeenLastCalledWith({ y: 900, animated: true });
    await m.run(() => m.a().lines(1200)); // the reply grows by 200: the room shrinks
    expect(m.a().spacer).toBe(276);
    await m.run(() => m.a().lines(1800)); // taller than the view
    expect(m.a().spacer).toBe(0);
    expect(m.scrollToEnd).not.toHaveBeenCalled();
    expect(m.scrollTo).toHaveBeenCalledTimes(1);
  });

  it('works the same however often the lines and the content report: the room is not read from the content', async () => {
    const m = await mountAnchor();
    await sent(m);
    for (const h of [1500, 1016, 1500, 1500]) await m.run(() => m.a().content(h));
    await m.run(() => m.a().lines(1000));
    expect(m.a().spacer).toBe(476);
  });

  it('puts the message back at the top when the view grows, as when the keyboard closes', async () => {
    const m = await mountAnchor();
    await sent(m);
    m.scrollTo.mockClear();
    await m.run(() => m.a().layout(900)); // 300 more room is needed
    expect(m.a().spacer).toBe(776);
    await m.run(() => m.a().content(1800));
    expect(m.scrollTo).toHaveBeenLastCalledWith({ y: 900, animated: false });
  });

  it('does not scroll while a finger is on the view', async () => {
    const m = await mountAnchor();
    await m.run(() => (m.a().lines(1000), m.a().content(1016)));
    await m.run(() => (m.a().start(), m.a().row(900)));
    await m.run(() => m.a().hold());
    await m.run(() => m.a().content(1500));
    expect(m.scrollTo).not.toHaveBeenCalled();
    await m.run(() => m.a().layout(900));
    await m.run(() => m.a().content(1800));
    expect(m.scrollTo).not.toHaveBeenCalled();
  });

  it('follows the end again once the person has scrolled to the bottom', async () => {
    const m = await mountAnchor();
    await sent(m);
    await m.run(() => (m.a().lines(1800), m.a().content(1816))); // the room is used up
    expect(m.a().scrolled(scrollEvent(900, 1816))).toBe(false); // our own jump is not read
    await m.run(() => m.a().hold());
    expect(m.a().scrolled(scrollEvent(1600, 1816))).toBe(true);
    await m.run(() => m.a().content(2400));
    expect(m.scrollToEnd).toHaveBeenCalled();
  });

  it("reading up while the reply grows keeps the reader's place", async () => {
    const m = await mountAnchor();
    await sent(m);
    await m.run(() => (m.a().lines(1800), m.a().content(1816)));
    await m.run(() => m.a().hold());
    expect(m.a().scrolled(scrollEvent(300, 1816))).toBe(false);
    m.scrollTo.mockClear();
    m.scrollToEnd.mockClear();
    await m.run(() => (m.a().lines(2400), m.a().content(2416)));
    expect(m.scrollTo).not.toHaveBeenCalled();
    expect(m.scrollToEnd).not.toHaveBeenCalled();
  });

  it('stacks messages sent before a reply under the first, which stays at the top', async () => {
    const m = await mountAnchor();
    await sent(m); // the first message is held at 900
    m.scrollTo.mockClear();
    await m.run(() => (m.a().lines(1100), m.a().start(true))); // a second one joins it: the top is not looked for again
    expect(m.a().spacer).toBe(376);
    await m.run(() => m.a().content(1500));
    expect(m.scrollTo).toHaveBeenLastCalledWith({ y: 900, animated: true });
    await m.run(() => (m.a().lines(1800), m.a().content(1816))); // the reply lands below them
    expect(m.a().spacer).toBe(0);
    expect(m.scrollTo).toHaveBeenCalledTimes(1);
  });
});

describe('entering the chat', () => {
  it('hides the lines until the last message is at the top, put there without a glide', async () => {
    const fresh = await mountAnchor(false);
    await fresh.run(() => (fresh.a().lines(1000), fresh.a().content(1016)));
    expect(fresh.a().placed).toBe(false); // not entered yet: nothing is placed
    await fresh.run(() => fresh.a().enter(true));
    await fresh.run(() => fresh.a().row(900));
    expect(fresh.a().spacer).toBe(476);
    expect(fresh.scrollTo).not.toHaveBeenCalled();
    expect(fresh.a().placed).toBe(false);
    await fresh.run(() => fresh.a().content(1500));
    expect(fresh.scrollTo).toHaveBeenCalledWith({ y: 900, animated: false });
    expect(fresh.a().placed).toBe(true);
  });

  it('holds the message even when its row was laid out before the chat was entered', async () => {
    const fresh = await mountAnchor(false);
    await fresh.run(() => (fresh.a().lines(1000), fresh.a().content(1016), fresh.a().row(900)));
    await fresh.run(() => fresh.a().enter(true));
    expect(fresh.a().spacer).toBe(476);
    await fresh.run(() => fresh.a().content(1500));
    expect(fresh.scrollTo).toHaveBeenCalledWith({ y: 900, animated: false });
    expect(fresh.a().placed).toBe(true);
  });

  it('puts the message at the top when the layout reports fractions of a pixel', async () => {
    const fresh = await mountAnchor(false);
    await fresh.run(() => (fresh.a().layout(592.6666870117188), fresh.a().enter(true)));
    await fresh.run(() => (fresh.a().lines(3013.333251953125), fresh.a().row(2706.666748046875)));
    await fresh.run(() => fresh.a().content(3299.333251953125)); // the spacer is whole pixels: the room can fall a hair short of the view
    expect(fresh.scrollTo).toHaveBeenCalledWith({ y: 2706.666748046875, animated: false });
    expect(fresh.a().placed).toBe(true);
  });

  it('shows the lines when a finger lands before the message is placed', async () => {
    const fresh = await mountAnchor(false);
    await fresh.run(() => (fresh.a().lines(1000), fresh.a().row(900), fresh.a().enter(true)));
    expect(fresh.a().placed).toBe(false);
    await fresh.run(() => fresh.a().hold());
    expect(fresh.a().placed).toBe(true);
  });

  it('follows the end when the person has sent nothing', async () => {
    const m = await mountAnchor();
    await m.run(() => (m.a().lines(1000), m.a().content(1016)));
    expect(m.scrollToEnd).toHaveBeenCalledWith({ animated: false });
    expect(m.a().placed).toBe(true);
  });
});

describe('the entry held at the top', () => {
  const run = (...kinds: string[]) => kinds.map((k, id) => ({ id, k }));
  const me = (e: { k: string }) => e.k === 'me';

  it('is the first of the last run of the person’s messages, before and after the reply', () => {
    expect(heldId(run('agent', 'me', 'agent', 'me', 'me', 'me'), me)).toBe(3);
    expect(heldId(run('agent', 'me', 'agent', 'me', 'me', 'me', 'agent', 'agent'), me)).toBe(3);
  });
  it('is the last message when a reply sits between them, and nothing when the person has sent none', () => {
    expect(heldId(run('me', 'agent', 'me', 'agent'), me)).toBe(2);
    expect(heldId(run('agent', 'agent'), me)).toBeUndefined();
    expect(heldId([], me)).toBeUndefined();
  });
});
