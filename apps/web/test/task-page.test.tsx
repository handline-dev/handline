// The task page: why it waits in one line, chips for owner, priority and time box, a folded brief, and one Activity list.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string, from: ParentNode = box) => from.querySelector<HTMLElement>(sel);
const $$ = (sel: string, from: ParentNode = box) => [...from.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const button = (label: string, within: ParentNode = box) => $$('button', within).find((b) => text(b).replace(/\s+/g, ' ').trim().startsWith(label));

async function mount(hash: string, s = setup()) {
  location.hash = hash;
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

describe('the task page', () => {
  it('shows why a task waits in one line, the owner, priority and time box as chips, and a long brief folded', async () => {
    const s = setup();
    const row1690 = (s.daemon.tasks as unknown as { rows: { id: number; body: string }[] }).rows.find((r) => r.id === 1690)!;
    row1690.body = 'A long brief. '.repeat(40);
    await mount('#/tasks/1690', s);
    await until(() => $('.twhy'), 'the waiting line');
    expect(text($('.twhy'))).toContain('blocked by T1688');
    expect(text($('.tchips'))).toMatch(/Owner · handline.auditor ▾.*Priority · P3 ▾.*Time box/);
    expect($('.line-clamp-4', $('article.tdetail')!)).not.toBeNull();
    await click(button('Show more', $('article.tdetail')!)!);
    expect($('.line-clamp-4', $('article.tdetail')!)).toBeNull();
  });

  it('lists comments and events as one Activity: the last three events, older ones folded, a note opened on "Read note"', async () => {
    const s = setup();
    const row = (s.daemon.tasks as unknown as { rows: { id: number; notes?: { at: string; who: string; text: string }[] }[] }).rows.find((r) => r.id === 1688)!;
    row.notes = [1, 2, 3, 4, 5].map((n) => ({ at: `2026-01-0${n}T10:00:00.000Z`, who: 'handline.pc', text: `note ${n}` }));
    await mount('#/tasks/1688', s);
    await until(() => $('section[aria-label="Activity"] .tevent'), 'the events');
    const act_ = $('section[aria-label="Activity"]')!;
    expect($$('.tevent', act_)).toHaveLength(3);
    expect(text(act_)).toContain('Show 2 earlier events');
    expect(text(act_)).toContain('handline.pc sent it back');
    expect(text(act_)).not.toContain('note 5');
    await click(button('Read note', act_)!);
    expect(text(act_)).toContain('note 3');
    await click(button('Show 2 earlier events', act_)!);
    expect($$('.tevent', act_)).toHaveLength(5);
  });
});
