// The notice bell in the status bar: the unread count, a click to the Inbox, the short result above the bell.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('the notice bell', () => {
  it('counts what is unread, opens the Inbox and clears on a click; a result shows above the bell', async () => {
    const s = setup();
    const store = new Store(s.client);
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={store} daemon="test" storage={null} />));
    const bell = () => box.querySelector<HTMLButtonElement>('.notice-bell')!;
    expect(bell().getAttribute('aria-label')).toBe('Notices; open the Inbox');
    await act(
      async () => void (store.notices.push('fail', 'Start failed: busy'), store.notices.push('info', 'a: task T1 is done', 'team')),
    );
    expect(bell().getAttribute('aria-label')).toBe('Notices, 2 unread; open the Inbox');
    expect(box.querySelector('.toasts')!.textContent).toContain('Start failed: busy');
    expect(box.querySelectorAll('.toast').length).toBe(2);
    expect(box.querySelector('[data-notice]')).toBeNull(); // no popover: the Inbox is the list
    await act(async () => bell().click());
    expect(location.hash).toBe('#/my-turn');
    expect(bell().getAttribute('aria-label')).toBe('Notices; open the Inbox');
  });
});
