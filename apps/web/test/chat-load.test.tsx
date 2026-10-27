// How a chat loads: the read goes out at once, and the skeleton and the lines never share a frame.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

const $ = (sel: string) => box.querySelector(sel);
const msg = (text: string) => [...box.querySelectorAll('.msg')].find((m) => m.textContent?.includes(text));
async function until(ok: () => unknown, what: string, ms = 3000) {
  for (let t = 0; t < ms; t += 10) {
    if (ok()) return;
    await act(async () => void (await new Promise((r) => setTimeout(r, 10))));
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function mount(s: ReturnType<typeof setup>) {
  history.replaceState(null, '', location.pathname);
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
}

describe('chat load', () => {
  it('reads the conversation while the agent detail is still on its way', async () => {
    const s = setup();
    const release = s.daemon.knobs.hold('agent.get');
    const call = vi.spyOn(s.client, 'call');
    await mount(s);
    await until(() => call.mock.calls.some(([m]) => m === 'chat.messages'), 'the conversation read, with agent.get held');
    expect($('.chat')).toBeNull(); // the tab itself waits for the detail
    release();
    await until(() => msg('Which gap list'), 'the conversation');
  });

  it('never draws the skeleton and the conversation in the same frame', async () => {
    const s = setup();
    const release = s.daemon.knobs.hold('chat.messages');
    await mount(s);
    await until(() => $('.chat .skeleton'), 'the chat skeleton');
    let both = false;
    const watch = new MutationObserver(() => void (both ||= !!$('.chat .skeleton') && !!msg('Which gap list')));
    watch.observe(box, { childList: true, subtree: true });
    release();
    await until(() => msg('Which gap list'), 'the conversation');
    await until(() => !$('.chat .skeleton'), 'the skeleton gone');
    watch.disconnect();
    expect(both).toBe(false);
  });

  it('asks for an older page only after the top has stayed in view for 0.3 s', async () => {
    const live = new Set<{ cb: (e: { isIntersecting: boolean }[]) => void }>();
    class Watch {
      cb: (e: { isIntersecting: boolean }[]) => void;
      constructor(cb: (e: { isIntersecting: boolean }[]) => void) {
        this.cb = cb;
      }
      observe() {
        live.add(this);
      }
      unobserve() {}
      disconnect() {
        live.delete(this);
      }
    }
    vi.stubGlobal('IntersectionObserver', Watch);
    const s = setup();
    for (let i = 0; i < 100; i++) s.daemon.chat.seed(ARCH, { from: 'human', to: ARCH, body: `filler ${i}` });
    await mount(s);
    await until(() => msg('filler 99') && $('.stream'), 'the newest page');
    const rows = () => box.querySelectorAll('.stream .msg[data-id]').length;
    const near = (isIntersecting: boolean) => act(async () => live.forEach((w) => w.cb([{ isIntersecting }])));
    const wait = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
    const first = rows();
    const stream = $('.stream') as HTMLElement;
    Object.defineProperty(stream, 'scrollHeight', { value: 5000, configurable: true });
    await act(async () => void stream.dispatchEvent(new Event('scroll'))); // the reader is up in the history
    await near(true);
    await wait(100);
    await near(false); // scrolled past before 0.3 s
    await wait(500);
    expect(rows()).toBe(first);
    await near(true);
    await wait(100);
    expect(rows()).toBe(first);
    await until(() => rows() > first, 'the older page after 0.3 s');
    expect(box.textContent).not.toContain('new message'); // an older page is no news
    vi.unstubAllGlobals();
  });
});
