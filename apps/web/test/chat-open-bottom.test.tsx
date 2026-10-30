// Opening a chat lands at its bottom, also when the lines grow (pictures taking their height) between our jump and the scroll event it raises.
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { useChatScroll } from '#src/useChatScroll.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const geometry = { lines: 1000, view: 800 };

function Chat() {
  const stream = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const scroll = useChatScroll({ stream, content, ready: true, agent: 'a' });
  return (
    <div ref={stream} data-testid="stream" onScroll={scroll.onScroll}>
      <div ref={content} />
    </div>
  );
}

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight;
  delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('opening a chat', () => {
  it('lands at the bottom ten times in a row, the lines growing before the jump reports its scroll', async () => {
    for (let load = 0; load < 10; load++) {
      geometry.lines = 1000;
      box = document.body.appendChild(document.createElement('div'));
      root = createRoot(box);
      await act(async () => {
        // The stream's measures are read by the hook as it enters.
        Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => geometry.lines });
        Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => geometry.view });
        root!.render(<Chat />);
      });
      const stream = box.querySelector<HTMLElement>('[data-testid="stream"]')!;
      expect(stream.scrollTop).toBe(200); // the entry jumped to the end of 1000 lines in a view of 800
      geometry.lines = 1320; // the pictures have their height now
      await act(async () => void stream.dispatchEvent(new Event('scroll')));
      expect(stream.scrollTop).toBe(520);
      await act(async () => root?.unmount());
      root = null;
      box.remove();
    }
  });
});
