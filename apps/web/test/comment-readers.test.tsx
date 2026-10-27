// The read button under a founder's comment: how many read it, each reader's state in a popover, Send now for a held one, gone when all have read.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('who has read a comment', () => {
  it('counts readers, lists each with its state, sends a held one now', async () => {
    const s = setup();
    s.daemon.tasks.verbs = true;
    const memo = s.daemon.tasks.memo({ task: 1688, text: 'Please use 16 px' }, 'add', 'human');
    s.daemon.tasks.setReaders(1688, memo, [
      { agent: 'handline.pc', request: 1, state: 'read', at: new Date().toISOString() },
      { agent: 'handline.ux', request: 2, state: 'held', at: new Date().toISOString(), minutes: 4 },
      { agent: 'handline.mobile', request: 3, state: 'sent', at: new Date().toISOString() },
    ]);
    location.hash = '#/tasks/1688';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    await until(() => $('.comment-readers'), 'the read button');
    const btn = $('.comment-readers')!;
    expect(btn.textContent).toContain('1/3 read');
    expect(btn.querySelector('[data-icon="waiting"]')).not.toBeNull(); // a clock while one is held
    await click(btn);
    const dialog = $('[role="dialog"][aria-label="Who has read this"]')!;
    expect(dialog.querySelector('[data-reader="handline.pc"]')!.textContent).toContain('ago');
    expect(dialog.querySelector('[data-reader="handline.ux"]')!.textContent).toContain('waits for the next pickup, in about 4 min');
    expect(dialog.querySelector('[data-reader="handline.mobile"]')!.textContent).toContain('sent, not read');
    const sendNow = [...dialog.querySelectorAll('button')].filter((b) => b.textContent === 'Send now');
    expect(sendNow).toHaveLength(1);
    await click(sendNow[0]);
    await until(
      () => $('.comment-readers')?.textContent?.includes('1/3 read') && !$('.comment-readers [data-icon="waiting"]'),
      'the sent state',
    );
  });
});
