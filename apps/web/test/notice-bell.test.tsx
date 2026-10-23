// The notice bell in the status bar: the unread count, the list that opens upward, the short result above the bell.
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
    expect(row.querySelector('[aria-label="handline.pc.auditor"]')!.textContent).toBe('au');
  });
});
