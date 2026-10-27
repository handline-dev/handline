// The notice bell in the status bar: the unread count, the list that opens upward, the short result above the bell.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
  it('counts what is unread, lists it by lane and clears on mark all read; a result shows above the bell', async () => {
    const s = setup();
    const store = new Store(s.client);
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={store} daemon="test" storage={null} />));
    const bell = () => box.querySelector<HTMLButtonElement>('.notice-bell')!;
    expect(bell().getAttribute('aria-label')).toBe('Notices');
    await act(
      async () => void (store.notices.push('fail', 'Start failed: busy'), store.notices.push('info', 'a: task T1 is done', 'team')),
    );
    expect(bell().getAttribute('aria-label')).toBe('Notices, 2 unread');
    expect(box.querySelector('.toasts')!.textContent).toContain('Start failed: busy');
    expect(box.querySelectorAll('.toast').length).toBe(2);
    await act(async () => bell().click());
    const rows = [...box.querySelectorAll('[data-notice]')].map((r) => r.textContent);
    expect(rows.join()).toContain('a: task T1 is done');
    expect(rows.join()).toContain('Start failed: busy');
    expect(box.textContent).not.toContain('Nothing yet'); // no empty sections, no lane headers
    expect(box.textContent).toContain('2 unread');
    await act(async () => [...box.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === 'Mark all read')!.click());
    expect(bell().getAttribute('aria-label')).toBe('Notices');
  });

  it('a finished task shows the closer, the task title and the result from the event alone', async () => {
    const s = setup();
    const store = new Store(s.client);
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={store} daemon="test" storage={null} />));
    await act(
      async () =>
        void store.notices.takeEvent({
          cursor: 9001,
          topic: 'notifications',
          type: 'task_done',
          agent: 'handline.pc.auditor',
          title: 'T5720 done',
          data: { task: 5720, task_title: 'Audit PR #536', result: 'PASS, real Chrome checked' },
        } as never),
    );
    await act(async () => box.querySelector<HTMLButtonElement>('.notice-bell')!.click());
    const row = box.querySelector('[data-notice]')!;
    expect(row.textContent).toContain('Audit PR #536');
    expect(row.textContent).toContain('T5720 done · PASS, real Chrome checked');
    expect(row.textContent).toContain('handline.pc.auditor'); // who closed it, in words
    expect(row.querySelector('svg')).not.toBeNull(); // the status as a shape, not only a colour
  });

  it('a toast opens what it is about and closes only itself; the X closes one; a failure offers Try again and Details; a bell row opens the same page', async () => {
    const s = setup();
    const store = new Store(s.client);
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={store} daemon="test" storage={null} />));
    const ev = (cursor: number, type: string, extra: object) =>
      ({ cursor, topic: 'notifications', type, agent: null, title: null, data: {}, ...extra }) as never;
    let again = 0;
    await act(async () => {
      store.notices.takeEvent(ev(9101, 'stall', { agent: 'handline.pc' }));
      store.notices.takeEvent(ev(9102, 'account_out', { data: { account: 'alpha' } }));
      store.notices.push('fail', "Couldn't start: busy", 'system', undefined, 'busy, retry later', () => void again++);
    });
    const toast = (text: string) => [...box.querySelectorAll<HTMLElement>('.toast')].find((t) => t.textContent!.includes(text))!;
    expect(box.querySelectorAll('.toast').length).toBe(3);
    expect(box.querySelector('[title="Click to dismiss"]')).toBeNull();
    expect(toast('busy').textContent).toContain('Details');
    expect(toast('blocked').textContent).toContain('Open its chat to see why.');
    expect(toast('blocked').querySelector('.toast-open')!.textContent).toBe('Open chat');
    expect(toast('alpha').querySelector('.toast-open')!.textContent).toBe('Open account');
    // Look A: a normal card on the neutral panel, a status icon first, its buttons below the text.
    for (const t of box.querySelectorAll<HTMLElement>('.toast')) {
      expect(t.className).toContain('bg-panel');
      expect(t.className).not.toMatch(/bg-ink|bg-bad/);
      expect(t.querySelector('svg')).not.toBeNull();
      expect(t.querySelector('.font-semibold')).not.toBeNull(); // a bold title
    }
    expect(toast('busy').textContent).toContain('busy, retry later'); // the second line is the daemon's first line
    await act(async () => toast('busy').querySelector<HTMLButtonElement>('.toast-retry')!.click());
    expect(again).toBe(1);
    expect(box.querySelectorAll('.toast').length).toBe(2);
    await act(async () => toast('alpha').querySelector<HTMLButtonElement>('.toast-close')!.click());
    expect(box.querySelectorAll('.toast').length).toBe(1);
    await act(async () => toast('blocked').querySelector<HTMLButtonElement>('.toast-open')!.click());
    expect(location.hash).toBe('#/agents/handline.pc');
    expect(box.querySelectorAll('.toast').length).toBe(0);
    // The bell: unread is bold until opened, and a row opens the same page.
    await act(async () => box.querySelector<HTMLButtonElement>('.notice-bell')!.click());
    const row = (text: string) => [...box.querySelectorAll<HTMLElement>('[data-notice]')].find((r) => r.textContent!.includes(text))!;
    expect(row('alpha').querySelector('.truncate.font-semibold')).not.toBeNull();
    expect(row('blocked').querySelector('.truncate.font-semibold')).toBeNull();
    await act(async () => row('alpha').click());
    expect(location.hash).toBe('#/accounts/alpha');
  });

  it('keyboard focus in a toast stops its clock; a hidden window stops them all', async () => {
    const s = setup();
    const store = new Store(s.client);
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={store} daemon="test" storage={null} />));
    await act(async () => void store.notices.push('ok', 'Applied'));
    const toast = box.querySelector<HTMLElement>('.toast')!;
    const hold = vi.spyOn(store.notices, 'hold');
    await act(async () => void toast.dispatchEvent(new FocusEvent('focusin', { bubbles: true })));
    expect(hold).toHaveBeenCalledWith(expect.any(Number), 'focus');
    const away = vi.spyOn(store.notices, 'setAway');
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    await act(async () => void document.dispatchEvent(new Event('visibilitychange')));
    expect(away).toHaveBeenLastCalledWith(true);
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  });

  it('Escape closes the newest toast, and the toasts hide while the bell is open', async () => {
    const s = setup();
    const store = new Store(s.client);
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={store} daemon="test" storage={null} />));
    await act(async () => {
      store.notices.push('need', 'first');
      store.notices.push('need', 'second');
    });
    expect(box.querySelectorAll('.toast').length).toBe(2);
    await act(async () => void box.querySelector<HTMLButtonElement>('.notice-bell')!.click());
    expect(box.querySelectorAll('.toast').length).toBe(0);
    await act(async () => void box.querySelector<HTMLButtonElement>('.notice-bell')!.click());
    expect(box.querySelectorAll('.toast').length).toBe(2);
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect(box.querySelectorAll('.toast').length).toBe(1);
  });
});
