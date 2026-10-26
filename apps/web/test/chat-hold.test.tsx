// The held message: entering a chat or sending puts the person's last message near the top, with room under it for the reply to grow into.
// jsdom has no layout, so the geometry is stood in by data attributes: the stream is 300 high, a message's top is `data-top` in the stream's own coordinates.
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { useHold } from '#src/useHold.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

beforeAll(() => {
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
  Object.defineProperty(proto, 'getBoundingClientRect', {
    configurable: true,
    value(this: HTMLElement) {
      const top = this.classList.contains('msg') ? Number(this.dataset.top) - Number(this.parentElement!.dataset.at ?? 0) : 0;
      return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: top, toJSON: () => ({}) };
    },
  });
  Object.defineProperty(proto, 'offsetHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return parseFloat(this.style.height || '0');
    },
  });
  Object.defineProperty(proto, 'clientHeight', { configurable: true, get: () => 300 });
  Object.defineProperty(proto, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return Number(this.dataset.lines ?? 0) + parseFloat(this.querySelector<HTMLElement>('.room')?.style.height || '0');
    },
  });
  Object.defineProperty(proto, 'scrollTop', {
    configurable: true,
    get(this: HTMLElement) {
      return Number(this.dataset.at ?? 0);
    },
    set(this: HTMLElement, v: number) {
      this.dataset.at = String(v);
    },
  });
});

let freeRef: { current: boolean };
const free = {
  get current() {
    return freeRef.current;
  },
  set current(v: boolean) {
    freeRef.current = v;
  },
};

function Harness({ held, lines, ready = true }: { held: number | undefined; lines: number; ready?: boolean }) {
  const stream = useRef<HTMLDivElement>(null);
  const own = useRef(false);
  const anchor = useRef(null);
  const pinned = useRef(true);
  const { spacer, start, free } = useHold({ stream, held, own, anchor, pinned, ready, agent: 'a' });
  freeRef = free;
  return (
    <div ref={stream} className="stream" data-lines={lines}>
      <div className="msg" data-id={1} data-top={200} />
      <div ref={spacer} className="room" />
      <button className="send" onClick={() => start()} />
    </div>
  );
}

let root: Root | null = null;
let box: HTMLElement;
const show = async (el: React.ReactElement) => {
  box ??= document.body.appendChild(document.createElement('div'));
  root ??= createRoot(box);
  await act(async () => root!.render(el));
};
const stream = () => box.querySelector<HTMLElement>('.stream')!;
const room = () => parseFloat(box.querySelector<HTMLElement>('.room')!.style.height);
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  box = undefined as never;
});

describe('the held message', () => {
  it('on entry the last sent message sits at the top with room under it for the reply', async () => {
    await show(<Harness held={1} lines={400} />);
    expect(stream().scrollTop).toBe(188); // its top, less the stream's padding
    expect(room()).toBe(88); // 188 + 300 - 400: just enough to put it there
  });

  it('a chat with no sent message gets no room and no scroll', async () => {
    await show(<Harness held={undefined} lines={400} />);
    expect(stream().scrollTop).toBe(0);
    expect(room()).toBeFalsy();
  });

  it('the room shrinks as the reply grows and the view stays where it is', async () => {
    await show(<Harness held={1} lines={400} />);
    await show(<Harness held={1} lines={450} />);
    expect(room()).toBe(38);
    await show(<Harness held={1} lines={600} />);
    expect(room()).toBe(0);
    expect(stream().scrollTop).toBe(188);
  });

  it('a late layout above the message does not move it off the top', async () => {
    await show(<Harness held={1} lines={400} />);
    stream().querySelector<HTMLElement>('.msg')!.dataset.top = '230'; // a title arrived and pushed it down
    await show(<Harness held={1} lines={430} />);
    expect(stream().scrollTop).toBe(218);
  });

  it('once the person scrolls, the view is theirs but the room is still kept', async () => {
    await show(<Harness held={1} lines={400} />);
    await act(async () => void (free.current = true));
    stream().scrollTop = 40; // the person scrolled
    await show(<Harness held={1} lines={420} />);
    expect(stream().scrollTop).toBe(40); // not put back
    expect(room()).toBe(68); // the room still follows the content
  });
});
