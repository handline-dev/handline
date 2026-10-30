// The update notice: one notice in the bottom-right stack that goes preparing, ready, later (the bell row keeps Restart now) or failed.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type ShellUpdate } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  delete (window as { handline?: unknown }).handline;
});

const view = (over: Partial<ShellUpdate>): ShellUpdate => ({ phase: 'idle', step: '', fraction: 0, reason: '', ...over });

describe('the update notice', () => {
  it('prepares with a step and a bar, asks to restart, keeps Restart now in the bell after Later, and says why it failed', async () => {
    const pressed: string[] = [];
    let send: (v: ShellUpdate) => void = () => undefined;
    (window as { handline?: unknown }).handline = {
      api: 1,
      onUpdate: (cb: (v: ShellUpdate) => void) => ((send = cb), () => undefined),
      updateAct: async (what: string) => (pressed.push(what), { ok: true }),
    };
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(setup().client)} daemon="test" storage={null} />));
    const toast = () => box.querySelector('.toasts')?.textContent ?? '';
    const button = (label: string) => [...box.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === label);
    const bell = () => box.querySelector<HTMLButtonElement>('.notice-bell')!;

    await act(async () => send(view({ phase: 'preparing', step: 'Building (2 of 3)', fraction: 0.5 })));
    expect(toast()).toContain('Updating Handline…');
    expect(toast()).toContain('Building (2 of 3)');
    expect(box.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')).toBe('50');
    await act(async () => box.querySelector<HTMLButtonElement>('.toast-close')!.click());
    await act(async () => send(view({ phase: 'preparing', step: 'Building (3 of 3)', fraction: 0.8 })));
    expect(toast()).not.toContain('Updating Handline…'); // closed: progress does not bring it back

    await act(async () => send(view({ phase: 'ready' })));
    expect(toast()).toContain('Handline is ready to restart');
    expect(box.querySelectorAll('.toast').length).toBe(1); // one notice, not two
    await act(async () => button('Later')!.click());
    expect(pressed).toEqual(['later']);
    await act(async () => send(view({ phase: 'later' })));
    expect(toast()).toBe('');
    expect(bell().getAttribute('aria-label')).toBe('Notices, 1 unread');
    await act(async () => bell().click());
    await act(async () => button('Restart now')!.click());
    expect(pressed).toEqual(['later', 'restart']);
    await act(async () => bell().click()); // the list closes, so toasts show again

    await act(async () => send(view({ phase: 'failed', reason: 'vite failed' })));
    expect(toast()).toContain('Handline was not updated');
    expect(toast()).toContain('vite failed');
    await act(async () => button('Details')!.click());
    await act(async () => button('Try again')!.click());
    expect(pressed.slice(-2)).toEqual(['details', 'retry']);

    await act(async () => send(view({ phase: 'idle' })));
    expect(toast()).toBe('');
  });
});
