// Reading the chat marks it: chat.seen goes up to the agent's newest message that was in view, and an unread divider stands where the person left off.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const msg = (body: string) => [...box.querySelectorAll<HTMLElement>('.msg')].find((m) => text(m).includes(body));
const chatOf = (t: { daemon: unknown }) =>
  (t.daemon as { chat: { messages: { id: number; body: string }[]; seenUpTo: Map<string, number> } }).chat;
const idOfBody = (t: { daemon: unknown }, body: string) => chatOf(t).messages.find((m) => m.body === body)!.id;

async function mount(agent: string, s = setup()) {
  history.replaceState(null, '', location.pathname);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('reading the chat', () => {
  /** The stream is 400 px high; a message is in it when its number is at most `inView`, else below it. */
  async function withRects(inView: number, run: () => Promise<void>) {
    const rect = Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect = function (this: Element) {
      const at = (top: number, bottom: number) => ({
        top,
        bottom,
        left: 0,
        right: 0,
        width: 0,
        height: bottom - top,
        x: 0,
        y: top,
        toJSON: () => ({}),
      });
      if (this.classList.contains('stream')) return at(0, 400);
      const id = Number((this as HTMLElement).dataset?.id);
      return this.classList.contains('msg') ? (id <= inView ? at(100, 140) : at(1000, 1040)) : at(0, 0);
    };
    try {
      await run();
    } finally {
      Element.prototype.getBoundingClientRect = rect;
    }
  }

  it("the agent's message leaves FOR YOU once it has been in view: chat.seen goes up to it", async () => {
    await withRects(Infinity, async () => {
      const t = await mount(ARCH);
      await until(() => msg('Which gap list'), 'history');
      await until(() => (chatOf(t).seenUpTo.get(ARCH) ?? 0) === idOfBody(t, 'Which gap list should come first?'), 'chat.seen');
    });
  });

  it('a message below the visible stream is not marked seen, and is once the person scrolls to it', async () => {
    await withRects(0, async () => {
      const t = await mount(ARCH);
      await until(() => msg('Which gap list'), 'history');
      await act(async () => void (await new Promise((r) => setTimeout(r, 60))));
      expect(chatOf(t).seenUpTo.get(ARCH)).toBeUndefined();
      await withRects(Infinity, async () => {
        await act(async () => void $('.stream')!.dispatchEvent(new Event('scroll')));
        await until(
          () => (chatOf(t).seenUpTo.get(ARCH) ?? 0) === idOfBody(t, 'Which gap list should come first?'),
          'chat.seen after the scroll',
        );
      });
    });
  });

  it('an unread divider stands above the first agent message past the cursor the chat opened with, and stays after chat.seen moves it', async () => {
    const s = setup();
    s.daemon.sessions.get(ARCH)!.fx.view = { founder_seen: 0 };
    await withRects(Infinity, async () => {
      const t = await mount(ARCH, s);
      await until(() => msg('Which gap list'), 'history');
      const divider = $('.unread-divider')!;
      expect(text(divider)).toBe('Unread from here');
      expect(divider.compareDocumentPosition(msg('Which gap list')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // above the message
      await until(() => (chatOf(t).seenUpTo.get(ARCH) ?? 0) > 0, 'chat.seen');
      expect($('.unread-divider')).not.toBeNull();
    });
  });

  it('there is no divider when everything of the agent was seen, or the daemon sends no cursor', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    fx.view = { founder_seen: 9999 };
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    expect($('.unread-divider')).toBeNull();
    fx.view = undefined;
  });
});
