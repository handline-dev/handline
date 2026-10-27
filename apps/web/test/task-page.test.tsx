// The task page: why it waits in one line, chips for owner, priority and time box, a folded brief, and one Activity list.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

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
const button = (label: string, within: ParentNode = box) =>
  $$('button', within).find((b) => text(b).replace(/\s+/g, ' ').trim().startsWith(label));

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
  vi.useRealTimers();
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

  it('does not say again, under the state chip, that a task waits on the founder', async () => {
    const s = setup();
    const row = (s.daemon.tasks as unknown as { rows: { id: number; to: string; state: string; blockers: unknown[] }[] }).rows.find(
      (r) => r.id === 1688,
    )!;
    Object.assign(row, { to: 'human', state: 'queued', blockers: [] });
    await mount('#/tasks/1688', s);
    await until(() => $('article.tdetail .tchips'), 'the chips');
    expect($('.twhy')).toBeNull();
  });

  it('lists comments and events as one Activity: the last three events, older ones folded, a note opened on "Read note"', async () => {
    const s = setup();
    const row = (s.daemon.tasks as unknown as { rows: { id: number; notes?: { at: string; who: string; text: string }[] }[] }).rows.find(
      (r) => r.id === 1688,
    )!;
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

  it('shows the brief, a Read note body and the waiting line as Markdown, not raw marks', async () => {
    const s = setup();
    const tasks = s.daemon.tasks as unknown as {
      rows: { id: number; body: string; notes?: { at: string; who: string; text: string }[] }[];
    };
    const r = tasks.rows.find((x) => x.id === 1688)!;
    r.body = '## Plan\n\n' + 'A long brief with **weight**. '.repeat(20);
    r.notes = [{ at: '2026-01-01T10:00:00.000Z', who: 'handline.pc', text: 'see **this**:\n- `a`\n- b' }];
    await mount('#/tasks/1688', s);
    await until(() => $('article.tdetail h2, article.tdetail h3, article.tdetail h4') && $('.tevent'), 'the page');
    const d = $('article.tdetail')!;
    expect(text(d)).not.toContain('## Plan');
    expect(text(d)).not.toContain('**weight**');
    await click(button('Read note', d)!);
    expect([...$$('.tevent strong', d)].map((b) => text(b))).toContain('this');
    expect($$('.tevent li', d).map((l) => text(l))).toEqual(['a', 'b']);
    expect($('section[aria-label="Activity"]')!.className).toContain('min-w-0'); // a long line cannot widen the list
  });

  it('lists what closed today under a folded "Done today" that opens on a click', async () => {
    vi.useFakeTimers({ toFake: ['Date'] }); // "1 h ago" and "3 days ago" land on the same days only at noon
    vi.setSystemTime(new Date(2026, 5, 15, 12, 0));
    const s = setup();
    const rows = (s.daemon.tasks as unknown as { rows: { id: number; done: string | null }[] }).rows;
    rows.find((r) => r.id === 1650)!.done = new Date(Date.now() - 3_600_000).toISOString();
    await mount('#/tasks', s);
    await until(() => $('tr.tgroup[data-group="done"]'), 'the Done today group');
    expect(text($('tr.tgroup[data-group="done"]'))).toContain('Done today 1 ▸');
    expect($('tr[data-task="1650"]')).toBeNull();
    await click(button('Done today')!);
    await until(() => $('tr[data-task="1650"]'), 'the done row');
  });

  it('splits Closed by the day a task closed', async () => {
    vi.useFakeTimers({ toFake: ['Date'] }); // "1 h ago" and "3 days ago" land on the same days only at noon
    vi.setSystemTime(new Date(2026, 5, 15, 12, 0));
    const s = setup();
    const rows = (s.daemon.tasks as unknown as { rows: { id: number; done: string | null }[] }).rows;
    rows.find((r) => r.id === 1650)!.done = new Date(Date.now() - 3 * 86_400_000).toISOString();
    await mount('#/tasks', s);
    await until(() => button('Closed'), 'the pills');
    await click(button('Closed')!);
    await until(() => $('tr.tgroup[data-group="done"]'), 'the Done groups');
    expect($$('tr.tgroup[data-group="done"]').map((g) => text(g).trim())).toEqual(['This week 1']);
  });
});
