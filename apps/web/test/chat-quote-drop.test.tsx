// Words selected in an agent's message offer Quote and the send carries them; a selection the daemon would refuse does not; files dragged over the composer say Drop to attach.
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
  window.getSelection()?.removeAllRanges();
  vi.restoreAllMocks();
});
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const msg = (body: string) => [...box.querySelectorAll<HTMLElement>('.msg')].find((m) => text(m).includes(body));
const typeInto = (el: HTMLTextAreaElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function mount(body: string) {
  const s = setup();
  s.daemon.chat.seed(ARCH, { from: ARCH, to: 'human', body, landing: 'delivered' });
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
  await until(() => msg(body.slice(0, 20)), 'the message');
  return s;
}
/** Selects `words` inside the message's text, as a drag over them would, and lets the mouse go. */
async function select(body: string, words: string) {
  const bubble = msg(body.slice(0, 20))!.querySelector<HTMLElement>('[data-id]') ?? msg(body.slice(0, 20))!;
  const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const at = n.textContent!.indexOf(words);
    if (at < 0) continue;
    const range = document.createRange();
    range.setStart(n, at);
    range.setEnd(n, at + words.length);
    window.getSelection()!.removeAllRanges();
    window.getSelection()!.addRange(range);
    break;
  }
  await act(async () => void bubble.dispatchEvent(new MouseEvent('mouseup', { bubbles: true })));
}

describe('quoting words of an agent message', () => {
  it('selecting words shows the Quote pop, and the send carries just those words', async () => {
    const body = 'The second gap is the one to cut first, then the rest can wait.';
    const s = await mount(body);
    const call = vi.spyOn(s.client, 'call');
    await select(body, 'the one to cut first');
    await until(() => $('.quote-pop'), 'the Quote pop');
    await act(async () => void $('.quote-pop')!.click());
    expect(text($('.compose-ref'))).toContain('the one to cut first');
    await typeInto($('.compose') as HTMLTextAreaElement, 'why this one');
    await act(
      async () => void $('.compose')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })),
    );
    await until(() => call.mock.calls.some(([m]) => m === 'chat.send'), 'the send');
    const sent = call.mock.calls.find(([m]) => m === 'chat.send')![1] as { quote?: string; parent?: number };
    expect(sent.quote).toBe('the one to cut first');
    expect(sent.parent).toBeGreaterThan(0);
  });

  it('a selection longer than the daemon takes offers no Quote', async () => {
    const body = `Start ${'long words '.repeat(120)}end`;
    await mount(body);
    await select(body, 'long words '.repeat(100).trim());
    await wait(50);
    expect($('.quote-pop')).toBeNull();
  });
});

describe('dropping files on the chat pane', () => {
  const drag = (el: Element, type: string, files: File[] = []) =>
    act(async () => {
      const e = new Event(type, { bubbles: true, cancelable: true });
      Object.assign(e, { dataTransfer: { types: ['Files'], files }, relatedTarget: null });
      el.dispatchEvent(e);
    });

  it('files dragged over the message stream cover the pane with Drop to attach, and leaving clears it', async () => {
    await mount('Report: gaps listed.');
    await drag($('.stream')!, 'dragenter');
    expect(text($('.chat'))).toContain('Drop to attach');
    expect($('.drop-cover')!.className).toContain('max-w-none!'); // .chat caps its children at 48rem; the cover is the whole pane
    await drag($('.stream')!, 'dragleave');
    expect(text($('.chat'))).not.toContain('Drop to attach');
  });

  it('a dropped file that is not a picture says Only pictures can be sent for now', async () => {
    await mount('Report: gaps listed.');
    await drag($('.stream')!, 'dragenter');
    await drag($('.stream')!, 'drop', [new File(['x'], 'notes.pdf', { type: 'application/pdf' })]);
    expect(text($('.chat'))).not.toContain('Drop to attach');
    const line = $('.compose-wrap [role="status"]')!;
    expect(line.textContent).toContain('Only pictures can be sent for now');
    expect($('.compose-wrap textarea')!.compareDocumentPosition(line) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
