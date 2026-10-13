// Search in the Chat tab head against the fake daemon: hits as lines, a click lands on the message, an empty result says so.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

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
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const text = (el: Element | null | undefined) => el?.textContent ?? '';

async function mount() {
  const store = new Store(setup().client);
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  await until(() => $('.chat-search input'), 'the search box');
}
const search = async (value: string) => {
  const input = $('.chat-search input') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await wait(300); // the box waits for the typing to stop
};

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('chat search', () => {
  it('lists the messages that hold the text and lands the chat on the one clicked', async () => {
    await mount();
    await until(() => $$('.msg').length, 'the chat');
    await search('contract');
    await until(() => $$('.chat-search .hit').length, 'a hit');
    const hits = $$('.chat-search .hit');
    expect(hits).toHaveLength(1);
    expect(text(hits[0])).toContain('Use the contract fixtures.');
    await act(async () => void hits[0]!.click());
    const shown = $$('.msg').find((m) => text(m).includes('Use the contract fixtures.'))!;
    expect(shown.hasAttribute('data-flash')).toBe(true);
    expect($('.chat-search .hit')).toBeNull();
  });

  it('says so when nothing holds the text', async () => {
    await mount();
    await search('zzz-nothing-says-this-zzz');
    await until(() => text($('.chat-search .results')).includes('No messages found'), 'the empty line');
    expect($$('.chat-search .hit')).toHaveLength(0);
  });

  it('searches the scope the tab shows: a message between two agents is not in Mine', async () => {
    await mount();
    await search('Review is done');
    await until(() => $('.chat-search .results'), 'the result box');
    await until(() => text($('.chat-search .results')).includes('No messages found'), 'no hit in Mine');
    await act(
      async () =>
        void $$('button')
          .find((b) => b.getAttribute('aria-label')?.startsWith('All'))
          ?.click(),
    );
    await search('');
    await search('Review is done');
    await until(() => $$('.chat-search .hit').length, 'the hit in All');
    expect(text($('.chat-search .hit'))).toContain('handline.auditor');
  });
});
