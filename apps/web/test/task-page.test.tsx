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
  it('shows what a task waits on once, the owner, priority and time box as chips, and a long brief folded', async () => {
    const s = setup();
    const row1690 = (s.daemon.tasks as unknown as { rows: { id: number; body: string }[] }).rows.find((r) => r.id === 1690)!;
    row1690.body = 'A long brief. '.repeat(40);
    await mount('#/tasks/1690', s);
    await until(() => $('[data-waiting-on]'), 'the waiting line');
    expect($('.twhy')).toBeNull(); // said once: the Waiting on line and a Blocked chip, not a second amber sentence
    expect(text($('section[aria-label="Time"], .tdetail'))).toContain('Blocked');
    expect(text($('.tchips'))).toMatch(/handline.auditor ▾.*P3 ▾.*Limit/);
    expect($('.line-clamp-4', $('article.tdetail')!)).not.toBeNull();
    await click(button('Show more', $('article.tdetail')!)!);
    expect($('.line-clamp-4', $('article.tdetail')!)).toBeNull();
  });

  it('marks a wait on tasks with one amber Blocked chip and its icon, and none when nothing blocks the task', async () => {
    await mount('#/tasks/1690');
    await until(() => $('[data-waiting-on]'), 'the waiting line');
    const chips = [...document.querySelectorAll('span')].filter(
      (e) => e.children.length < 2 && text(e) === 'Blocked' && e.className.includes('rounded-sm'),
    );
    expect(chips).toHaveLength(1);
    expect(chips[0]!.className).toContain('text-wait');
    expect(chips[0]!.querySelector('svg')).not.toBeNull();
    await act(async () => void (location.hash = '#/tasks/1688')); // local-ref-ok: a task nothing blocks
    await until(() => $('article[aria-label="Task T1688"]') && !$('[data-waiting-on]'), 'the unblocked task');
    expect(text($('article.tdetail'))).not.toContain('Blocked');
  });

  it("sets the task number in the title's own face, with no mono class", async () => {
    await mount('#/tasks/1690');
    await until(() => $('.title-slot'), 'the title');
    const ref = $('.title-slot')!.previousElementSibling!;
    expect(text(ref)).toBe('T1690'); // local-ref-ok
    expect(ref.className).not.toMatch(/mono|text-\[/);
    expect(ref.parentElement!.className).toContain('items-baseline');
  });

  it('says "Not picked up yet" as a quiet line, not a boxed warning, when only the daemon has not looked at the task', async () => {
    const s = setup();
    const row = (
      s.daemon.tasks as unknown as { rows: { id: number; to: string; state: string; blockers: unknown[]; hold_reason: string }[] }
    ).rows.find((r) => r.id === 1688)!;
    Object.assign(row, {
      to: 'handline.architect',
      state: 'queued',
      blockers: [],
      hold_reason: 'waiting: the daemon has not looked at it yet - it does on its next tick',
    });
    await mount('#/tasks/1688', s);
    await until(() => $('.twhy'), 'the waiting line');
    const why = $('.twhy')!;
    expect(text(why).trim()).toBe('Not picked up yet');
    expect(why.className).not.toContain('border-wait');
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

  it('lists comments and events as one Activity: the last three events, older ones folded, a note shown open under its line', async () => {
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
    expect(text(act_)).toContain('note 5'); // the notes of the shown events are open
    expect(text(act_)).not.toContain('note 1'); // the folded ones are not
    expect(button('Read note', act_)).toBeUndefined(); // no link to click: the note is the content
    expect(text(act_)).toContain('note 3');
    await click(button('Show 2 earlier events', act_)!);
    expect($$('.tevent', act_)).toHaveLength(5);
  });

  it('shows the brief, an open note body and the waiting line as Markdown, not raw marks', async () => {
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
    expect([...$$('.tevent strong', d)].map((b) => text(b))).toContain('this');
    expect($$('.tevent li', d).map((l) => text(l))).toEqual(['a', 'b']);
    expect($('section[aria-label="Activity"]')!.className).toContain('min-w-0'); // a long line cannot widen the list
  });

  it('folds a note longer than eight lines to its first lines with a Show more, and short ones stay open', async () => {
    const s = setup();
    const r = (s.daemon.tasks as unknown as { rows: { id: number; notes?: { at: string; who: string; text: string }[] }[] }).rows.find(
      (x) => x.id === 1688,
    )!;
    r.notes = [
      { at: '2026-01-01T10:00:00.000Z', who: 'handline.pc', text: Array.from({ length: 12 }, (_, i) => `line ${i + 1}`).join('\n\n') },
    ];
    await mount('#/tasks/1688', s);
    await until(() => $('.tnote'), 'the note');
    const note = $('.tnote')!;
    expect(text(note)).toContain('line 4');
    expect(text(note)).not.toContain('line 12');
    expect(text(note)).toContain('Show more');
    await click(button('Show more', note)!);
    expect(text(note)).toContain('line 12');
    expect(text(note)).toContain('Show less');
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
