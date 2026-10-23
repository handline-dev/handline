import { describe, expect, it } from 'vitest';
import { ARCH, $, mount, press, sleep, until } from '#test/harness.tsx';

const card = (task: number, event: 'opened' | 'done', who = ARCH) => ({
  kind: 'task_event' as const,
  task_event: { event, task, request: null, evidence: event === 'done' ? 'auditor PASS' : null, who },
});

const cards = () =>
  [...document.body.querySelectorAll('[data-testid^="task-event-"]')].filter((e) => e.textContent?.includes('T900')).length;

describe('a run of task cards in the chat', () => {
  it('is one line with its counts that opens in place; two cards stay cards', async () => {
    const m = await mount(
      (s) => {
        for (const t of [9001, 9002, 9003]) s.daemon.chat.seedItem(ARCH, card(t, 'opened'));
        s.daemon.chat.seedItem(ARCH, card(9003, 'done'));
      },
      undefined,
      'founder',
    );
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    const toggle = () => document.body.querySelector<HTMLElement>('[data-testid^="fold-toggle-"]');
    await until(toggle, 'the fold line');
    expect(toggle()!.textContent).toContain('3 tasks · 3 added · 1 done');
    expect(cards()).toBe(0);
    await sleep(100); // a later render
    expect(toggle()!.textContent).not.toContain('new'); // it was there when the chat opened
    await press(toggle());
    await until(() => cards() === 3, 'the cards open in place'); // the added card of task 3 is merged into its done card
    await press(toggle());
    await until(() => cards() === 0, 'the cards fold');
    m.store.stop();
  });

  it('keeps a run of two as cards', async () => {
    const m = await mount(
      (s) => {
        for (const t of [9001, 9002]) s.daemon.chat.seedItem(ARCH, card(t, 'opened'));
      },
      undefined,
      'founder',
    );
    await until(() => $(`agent-${ARCH}`), 'the rows');
    await press($(`agent-${ARCH}`));
    await until(() => cards() === 2, 'two cards');
    expect(document.body.querySelector('[data-testid^="fold-toggle-"]')).toBeNull();
    m.store.stop();
  });
});
