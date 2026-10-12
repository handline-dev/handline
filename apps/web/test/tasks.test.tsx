// U11: the Tasks page on app-data, against the fake daemon's tasks with its latency and refusal knobs.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
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
const memory = (): KV & { data: Map<string, string> } => {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string, from: ParentNode = box) => from.querySelector<HTMLElement>(sel);
const $$ = (sel: string, from: ParentNode = box) => [...from.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const typeInto = (el: HTMLTextAreaElement | HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const choose = (el: HTMLSelectElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
const press = (key: string, init: KeyboardEventInit = {}, el: Element = document.activeElement ?? box) =>
  act(async () => void el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })));
const page = () => $('section.tasks')!;
const row = (id: number) => $(`tr[data-task="${id}"]`);
const rowIds = () => $$('tr.trow').map((r) => Number(r.dataset.task));
const button = (label: string, within: ParentNode = box) =>
  $$('button', within).find((b) => (text(b).replace(/\s+/g, ' ').trim() || b.getAttribute('aria-label') || '').startsWith(label));
const sheet = (label: string) => $$('.tsheet').find((s) => s.getAttribute('aria-label') === label);

const withVerbs = (s: ReturnType<typeof setup>) => ((s.daemon.tasks.verbs = true), s);

async function mount(hash = '#/tasks', s = setup(), storage: KV = memory()) {
  const store = new Store(s.client);
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  return { store, storage, ...s };
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});
const loaded = () => until(() => rowIds().length, 'the task rows');
const focusPage = () => act(async () => page().focus());
const cursorTo = async (id: number) => {
  await focusPage(); // a click on a row opens the task, so the cursor goes there by key
  const from = rowIds().indexOf(id) - rowIds().findIndex((r) => row(r)!.classList.contains('cursor'));
  for (let i = 0; i < Math.abs(from); i++) await press(from > 0 ? 'j' : 'k');
};
const mark = (id: number) => click($(`input[aria-label="Mark T${id}"]`));

describe('the list', () => {
  it('shows the open tasks sorted by priority, with the columns of the bridge, and leaves done ones out until asked', async () => {
    await mount();
    await loaded();
    expect(rowIds()).toEqual([1684, 1688, 1700, 1690]); // P1, then P2 by number, then P3
    expect(
      $$('th').map((h) =>
        text(h)
          .trim()
          .replace(/ [▴▾]$/, ''),
      ),
    ).toEqual(['', '#', 'P', 'state', 'title', 'time', '']);
    expect(text(row(1684))).toContain('P1');
    expect(text(row(1688))).toContain('doing');
    expect(text(row(1684))).toContain('handline.architect');
    expect(text(row(1688))).toMatch(/left/); // a running task shows its time left
    expect(row(1650)).toBeNull();
    await click(button('Closed')!);
    await until(() => row(1650), 'the done task');
    expect(text(row(1650))).toContain('done');
    expect(row(1688)).toBeNull(); // Closed shows only the closed ones
  });

  it('sorts by a column head and by the s key, and reverses with S', async () => {
    await mount();
    await loaded();
    await click(button('#')!);
    expect(rowIds()).toEqual([1684, 1688, 1690, 1700]);
    await click(button('#')!);
    expect(rowIds()).toEqual([1700, 1690, 1688, 1684]);
    expect($('th[aria-sort="descending"]')).not.toBeNull();
    await focusPage();
    await press('S'); // next column from number is priority, reversed
    expect(rowIds()).toEqual([1690, 1688, 1700, 1684]);
  });

  it('moves the cursor with j and k, marks with space and A, and keeps its marks in the action bar', async () => {
    await mount();
    await loaded();
    await focusPage();
    expect(row(1684)!.classList.contains('cursor')).toBe(true);
    await press('j');
    expect(row(1688)!.classList.contains('cursor')).toBe(true);
    await press(' ');
    expect(($('input[aria-label="Mark T1688"]') as HTMLInputElement).checked).toBe(true); // local-ref-ok
    expect(text($('.tbar'))).toContain('1 marked');
    await press('A');
    expect(text($('.tbar'))).toContain('4 marked');
    await press('A');
    expect(text($('.tbar'))).not.toContain('marked');
    await press(' ');
    await press('Escape'); // clears the marks
    expect(text($('.tbar'))).not.toContain('marked');
  });

  it('shows the tree under the parent, remembers the choice, and a hidden parent stays as a dimmed row', async () => {
    const storage = memory();
    await mount('#/tasks', undefined, storage);
    await loaded();
    await focusPage();
    await press('t');
    expect(rowIds()).toEqual([1684, 1688, 1690, 1700]);
    expect($$('th').map((h) => text(h).trim())).toEqual(['', 'task', 'state', '']);
    expect(($('td.c-title', row(1688)!) as HTMLElement).style.paddingLeft).toBe('26px');
    expect(text(row(1688))).toContain('T1684'); // local-ref-ok
    expect(storage.data.get('tasksView')).toBe('tree');
    await click($('.ui-seg[aria-label="View"] button')!); // back to the list
    expect(rowIds()).toEqual([1684, 1688, 1700, 1690]);
  });

  it('searches the number, title, owner and asker, every word having to match', async () => {
    await mount();
    await loaded();
    const search = $('input[aria-label="Search tasks"]') as HTMLInputElement;
    await typeInto(search, 'terminal pane');
    expect(rowIds()).toEqual([1688, 1690]); // two words, both in the title
    await typeInto(search, 'handline.auditor');
    expect(rowIds()).toEqual([1690]); // the owner
    await typeInto(search, '1700');
    expect(rowIds()).toEqual([1700]); // the number
    await typeInto(search, 'terminal founder');
    expect(rowIds()).toEqual([]); // every word must match
    await typeInto(search, '');
    expect(rowIds()).toEqual([1684, 1688, 1700, 1690]);
  });

  it('filters by agent and by state in the Filters popover, with f to step through the agents and v to open it', async () => {
    await mount();
    await loaded();
    const agent = () => $('select[aria-label="Agent"]') as HTMLSelectElement;
    expect(agent()).toBeNull(); // the popover is closed
    await click(button('Filters')!);
    await choose(agent(), 'handline.auditor');
    expect(rowIds()).toEqual([1690]);
    expect($('.tdot')).not.toBeNull(); // a filter is on, said by a mark and not by colour
    expect($('button[aria-label="Filters, on"]')).not.toBeNull(); // and said to a screen reader
    await choose(agent(), '');
    expect($('.tdot')).toBeNull();
    await press('Escape');
    expect(agent()).toBeNull();
    await focusPage();
    await press('f');
    await press('v');
    expect(agent().value).not.toBe('');
    await choose(agent(), '');
    await click($$('.ui-popup input').find((i) => text(i.parentElement).includes('draft'))!);
    expect(rowIds()).not.toContain(1700);
    await click(button('Reset', $('.ui-popup')!)!);
    expect(rowIds()).toContain(1700);
  });

  it('has the Open, Mine and Closed pills: Mine is what I own', async () => {
    const s = setup();
    await mount('#/tasks', s);
    await loaded();
    const me = s.client.hello!.caller.name;
    await click(button('Mine')!);
    expect(
      rowIds().every(
        (id) => (s.daemon.tasks as unknown as { rows: { id: number; to: string }[] }).rows.find((r) => r.id === id)!.to === me,
      ),
    ).toBe(true);
    expect($('[role=radio][aria-checked=true]')!.textContent).toBe('Mine'); // the chosen pill is raised and marked, not only coloured
    await click(button('Open')!);
    expect(rowIds().length).toBe(4);
  });

  it('opens the key sheet with ?, listing the keys of this page', async () => {
    await mount();
    await loaded();
    await focusPage();
    await press('?');
    const keys = sheet('Keys on this page')!;
    for (const k of ['Space', 'Enter', 'd', 'o', 'p', 'm', 's / S', 'a', 'v', 't', 'f']) expect($$('kbd', keys).map(text)).toContain(k);
    await press('Escape', {}, keys);
    expect(sheet('Keys on this page')).toBeUndefined();
  });
});

describe('loading, empty and not served', () => {
  it('draws the table skeleton after the delay, no spinner, in the real row height, then the rows', async () => {
    const s = setup();
    s.daemon.knobs.latency(500);
    await mount('#/tasks', s);
    await until(() => $('.tasks .skeleton'), 'the skeleton');
    expect($('.tasks .skeleton')!.getAttribute('aria-busy')).toBe('true');
    expect($('.spinner, [role=progressbar]')).toBeNull();
    expect($$('.tasks .skeleton > div').length).toBe(10);
    expect(($('.tasks .skeleton > div') as HTMLElement).style.height).toBe('48px');
    await until(() => rowIds().length, 'the rows', 600);
  });

  it('says what is empty and offers the way on', async () => {
    const s = setup();
    (s.daemon.tasks as unknown as { rows: { state: string }[] }).rows.forEach((r) => (r.state = 'done'));
    await mount('#/tasks', s);
    await until(() => text(page()).includes('No tasks in these states.'), 'the empty line');
    await click(button('Show closed')!);
    await until(() => rowIds().length === 5, 'every task');
  });

  it("shows the daemon's own sentence with Retry on a refusal, and the rows come back when it answers again", async () => {
    const s = setup();
    s.daemon.knobs.refuse('tasks.list', 'state', 'the task store is locked', undefined, 1);
    await mount('#/tasks', s);
    await until(() => $('.banner'), 'the error strip');
    expect(text($('.banner'))).toContain('the task store is locked');
    expect(rowIds()).toEqual([]);
    await click(button('Retry')!);
    await until(() => rowIds().length === 4, 'the rows');
    expect($('.banner')).toBeNull();
  });
});

describe('writes', () => {
  it('shows a new priority at once with a hollow dot, then settles it; a refusal puts it back and says "not changed"', async () => {
    const s = setup();
    await mount('#/tasks', s);
    await loaded();
    await cursorTo(1700);
    const release = s.daemon.knobs.hold('task.edit');
    await press('p');
    await click(button('1 high', sheet('priority (1 high, 2, 3 low):')!)!);
    await until(() => text(row(1700)).includes('P1'), 'P1 shown at once');
    expect(row(1700)!.classList.contains('pending')).toBe(true);
    expect($('.c-state svg[data-icon="unsaved"]', row(1700)!)).not.toBeNull(); // hollow dot, a shape
    expect(s.daemon.tasks.get({ task: 1700 }).priority).toBeUndefined(); // the daemon has not answered yet
    release();
    await until(() => s.daemon.tasks.get({ task: 1700 }).priority === 1, 'the daemon changed it');
    await until(() => !row(1700)!.classList.contains('pending'), 'settled');
    // now a refusal
    s.daemon.knobs.refuse('task.edit', 'state', 'task 1700 is locked', undefined, 1);
    await press('p');
    await click(button('3 low', sheet('priority (1 high, 2, 3 low):')!)!);
    await until(() => text(row(1700)).includes('not changed'), 'not changed');
    expect(text(row(1700))).toContain('P1'); // back to what it was
    expect($('.toast')?.textContent).toContain('task 1700 is locked');
  });

  it('changes the owner of the marked tasks, and says "1 of 2 changed" when one is refused', async () => {
    const s = setup();
    await mount('#/tasks', s);
    await loaded();
    await focusPage();
    await mark(1684);
    await mark(1700);
    s.daemon.knobs.refuse('task.edit', 'state', 'cannot move', undefined, 1);
    await press('o');
    await choose($('select[aria-label="New owner"]') as HTMLSelectElement, 'handline.auditor');
    await click(button('Change owner')!);
    await until(() => text($('.toast')).includes('1 of 2 changed'), 'the partial toast');
    expect($$('.not-changed')).toHaveLength(1);
    const owners = [1684, 1700].map((id) => s.daemon.tasks.get({ task: id }).to);
    expect(owners.filter((o) => o === 'handline.auditor')).toHaveLength(1);
  });

  it('sends a new task once on Cmd+Enter from a field, and keeps focus in the field while typing', async () => {
    const s = setup();
    await mount('#/tasks', s);
    await loaded();
    await focusPage();
    await press('n');
    await choose($('.ui-dialog select') as HTMLSelectElement, 'handline.auditor');
    const title = $('.ui-dialog .tform input') as HTMLInputElement;
    await act(async () => title.focus());
    await typeInto(title, 'Once only');
    expect(document.activeElement).toBe(title);
    await press('Enter', { metaKey: true });
    await until(() => $$('tr.trow').some((r) => text(r).includes('Once only')), 'the new row');
    await wait(50);
    expect($$('tr.trow').filter((r) => text(r).includes('Once only'))).toHaveLength(1);
  });

  it('makes a new task: it is in the list at once with a hollow dot, and a refusal keeps what was typed', async () => {
    const s = setup();
    await mount('#/tasks', s);
    await loaded();
    await focusPage();
    await press('n');
    await click(button('Ask')!);
    expect(text($('.ui-dialog [role=alert]'))).toContain('Pick who it is for.');
    await choose($('.ui-dialog select') as HTMLSelectElement, 'handline.auditor');
    await typeInto($('.ui-dialog .tform input') as HTMLInputElement, 'Check the lint doc');
    s.daemon.knobs.refuse('task.new', 'quota', 'too many open tasks', undefined, 1);
    await click(button('Ask')!);
    await until(() => text($('.toast')).includes('too many open tasks'), 'the refusal');
    expect(text($('.ui-dialog [role=alert]'))).toContain('too many open tasks'); // the dialog says it too, not only the toast
    expect(($('.ui-dialog .tform input') as HTMLInputElement).value).toBe('Check the lint doc'); // still in the box
    expect($$('tr.trow').some((r) => text(r).includes('Check the lint doc'))).toBe(false); // the row went again
    s.daemon.knobs.latency(300);
    await click(button('Ask')!);
    await until(() => $$('tr.trow').some((r) => text(r).includes('Check the lint doc')), 'the new row');
    const mine = $$('tr.trow').find((r) => text(r).includes('Check the lint doc'))!;
    expect(mine.classList.contains('pending')).toBe(true);
    expect(text(mine)).toContain('new');
    await until(() => text($$('tr.trow').find((r) => text(r).includes('Check the lint doc'))).includes('T2000'), 'its number', 600); // local-ref-ok
    expect($('.ui-dialog')).toBeNull(); // the form closed on success
  });

  it("acts on a row from the row's own icons, with no bar under the table until rows are marked", async () => {
    await mount();
    await loaded();
    expect($('.tbar')).toBeNull();
    await click(button('Priority', row(1700)!)!);
    expect(sheet('priority (1 high, 2, 3 low):')).toBeDefined();
    expect(row(1700)!.classList.contains('cursor')).toBe(true); // the sheet is for that row
  });

  it('keeps Discard and Merge disabled, with the reason, until the daemon serves them', async () => {
    await mount();
    await loaded();
    const d = button('Discard', row(1684)!)!; // the row's own icon
    expect((d as HTMLButtonElement).disabled).toBe(true);
    expect(d.title).toBe('This daemon does not serve task.discard yet');
    await mark(1684);
    expect(button('Merge')!.title).toBe('This daemon does not serve task.merge yet');
    expect((button('Owner') as HTMLButtonElement).disabled).toBe(false);
    await focusPage();
    await press('d');
    expect($('.tsheet')).toBeNull(); // the key does nothing either
  });

  it('discards with a required reason, saying "stop it and discard" for a running task, and shows "…" until the daemon answers', async () => {
    const s = withVerbs(setup());
    await mount('#/tasks', s);
    await loaded();
    await cursorTo(1688); // doing
    await press('d');
    const ask = sheet('stop it and discard - why:')!;
    await click(button('Discard', ask)!);
    expect(text($('.tsheet-err'))).toContain('A reason is required.'); // an empty reason keeps the sheet open
    await typeInto($('input', ask) as HTMLInputElement, 'not needed');
    const release = s.daemon.knobs.hold('task.discard');
    await click(button('Discard', ask)!);
    await until(() => text($('.c-state', row(1688)!)).includes('…'), 'the decided mark');
    expect(row(1688)!.classList.contains('pending')).toBe(false); // the row itself is not changed
    expect(text($('.c-state', row(1688)!))).not.toContain('doing');
    release();
    await until(() => !row(1688), 'the row gone');
  });

  it('folds the marked tasks into the row under the cursor, naming both ends', async () => {
    const s = withVerbs(setup());
    await mount('#/tasks', s);
    await loaded();
    await mark(1700);
    await mark(1688);
    await cursorTo(1690);
    await press('m');
    expect(sheet('fold 2 into T1690?')).toBeDefined(); // local-ref-ok
    const merges: unknown[] = [];
    const fold = s.daemon.tasks.merge.bind(s.daemon.tasks);
    s.daemon.tasks.merge = (p) => (merges.push(p), fold(p));
    await click(button('Fold')!);
    await until(() => !row(1700) && !row(1688), 'the folded rows gone');
    expect(rowIds()).toEqual([1684, 1690]);
    expect(merges).toHaveLength(1);
  });
});

describe('the detail', () => {
  it('opens from a row with Enter, shows the task whole, and goes back with Escape', async () => {
    await mount();
    await loaded();
    await cursorTo(1688);
    await press('Enter');
    await until(() => $('article.tdetail') && text($('article.tdetail')).includes('Build the pane.'), 'the detail');
    expect(location.hash).toBe('#/tasks/1688');
    const d = $('article.tdetail')!;
    expect(text(d)).toContain('Terminal pane on the bytes channel');
    expect(text(d)).toContain('Asked by');
    expect(text(d)).toContain('handline.architect');
    expect(text(d)).toContain('T1684'); // under, local-ref-ok
    expect(text(d)).toContain('History');
    await press('Escape', {}, $('.tbody')!);
    await until(() => location.hash === '#/tasks', 'the list again');
  });

  it('shows what a task waits on and its sub-tasks as links, and says so when the task is gone', async () => {
    await mount('#/tasks/1690');
    await until(() => text($('article.tdetail')).includes('waits for the pane'), 'blockers');
    expect($$('a', $('article.tdetail')!).map((a) => a.getAttribute('href'))).toContain('#/tasks/1688');
    await act(async () => void (location.hash = '#/tasks/1684'));
    await until(() => text($('article.tdetail')).includes('Sub-tasks'), 'children');
    await act(async () => void (location.hash = '#/tasks/9999'));
    await until(() => text($('article.tdetail')).includes('This task no longer exists'), 'gone');
  });

  it('draws the detail skeleton while it loads', async () => {
    const s = setup();
    s.daemon.knobs.latency(500);
    await mount('#/tasks/1688', s);
    await until(() => $('article.tdetail .skeleton'), 'the detail skeleton');
    await until(() => text($('article.tdetail')).includes('Build the pane.'), 'the detail', 600);
  });

  it('keeps the verbs the daemon does not serve yet disabled, with the reason, and hides those that do not apply', async () => {
    await mount('#/tasks/1688');
    await until(() => $('.tverbs'), 'the verbs');
    const verbs = $('.tverbs')!;
    for (const [label, method] of [
      ['Done', 'task.done'],
      ['Discard', 'task.discard'],
    ] as const) {
      expect((button(label, verbs) as HTMLButtonElement).disabled).toBe(true);
      expect(button(label, verbs)!.title).toBe(`This daemon does not serve ${method} yet`);
    }
    expect((button('Edit', verbs) as HTMLButtonElement).disabled).toBe(false);
    expect((button('Cancel', verbs) as HTMLButtonElement).disabled).toBe(false);
    expect(button('Reopen', verbs)).toBeUndefined(); // not a done task
    expect(button('Queue', verbs)).toBeUndefined(); // not a draft
    expect(button('Withdraw', verbs)).toBeUndefined(); // already running, and not mine
  });

  it('edits title, owner, time box and priority at once, and puts it back when the daemon refuses', async () => {
    const s = setup();
    await mount('#/tasks/1700', s);
    await until(() => $('.tverbs'), 'the verbs');
    await click(button('Edit')!);
    const form = sheet('Edit T1700')!; // local-ref-ok
    const [title] = $$('input.tinput', form);
    await typeInto(title as HTMLInputElement, 'Pair the web UI');
    await choose($('select', form) as HTMLSelectElement, '1');
    const release = s.daemon.knobs.hold('task.edit');
    await click(button('Save', form)!);
    await until(() => text($('.tdetail-head')).includes('Pair the web UI'), 'the new title at once');
    expect(s.daemon.tasks.get({ task: 1700 }).title).toContain('Draft'); // not yet at the daemon
    release();
    await until(() => s.daemon.tasks.get({ task: 1700 }).title === 'Pair the web UI', 'saved');
    expect(s.daemon.tasks.get({ task: 1700 }).priority).toBe(1);
    s.daemon.knobs.refuse('task.edit', 'state', 'locked');
    await click(button('Edit')!);
    await typeInto($$('input.tinput', sheet('Edit T1700')!)[0] as HTMLInputElement, 'Nope'); // local-ref-ok
    await click(button('Save', sheet('Edit T1700')!)!); // local-ref-ok
    await until(() => text($('.toast')).includes('locked'), 'the refusal');
    await until(() => text($('.tdetail-head')).includes('Pair the web UI'), 'the title back');
    expect($('.tsheet')).not.toBeNull(); // the form stays with the person's text
  });

  it('withdraws a task I asked for after a confirmation, with "…" until the daemon answers; a started task can only be cancelled', async () => {
    const s = setup();
    await mount('#/tasks', s);
    await loaded();
    await focusPage();
    await press('n');
    await choose($('.ui-dialog select') as HTMLSelectElement, 'handline.auditor');
    await typeInto($('.ui-dialog .tform input') as HTMLInputElement, 'Mine to take back');
    await click(button('Ask')!);
    await until(() => $$('tr.trow').some((r) => text(r).includes('T2000')), 'its number'); // local-ref-ok
    await act(async () => void (location.hash = '#/tasks/2000'));
    await until(() => button('Withdraw', $('.tverbs') ?? box), 'Withdraw');
    await click(button('Withdraw', $('.tverbs')!)!);
    const ask = sheet('withdraw T2000?')!; // local-ref-ok
    s.daemon.knobs.latency(300);
    await click(button('Withdraw', ask)!);
    await until(() => text($('.tdetail-head')).includes('…'), 'the decided mark');
    await until(() => text($('.tdetail-head')).includes('discarded'), 'withdrawn', 600);
    expect(button('Cancel', $('.tverbs')!)).toBeUndefined(); // closed: no verbs left that stop it
  });

  it('replies to someone the task names: pick the chip, type, send', async () => {
    const s = withVerbs(setup());
    await mount('#/tasks/1688', s);
    const reply = () => $('section[aria-label="Reply to someone"]')!;
    await until(() => $('section[aria-label="Reply to someone"]'), 'the reply box');
    expect($('[aria-label="Add a memo"]')).toBeNull();
    expect((button('Send', reply()) as HTMLButtonElement).disabled).toBe(true); // nobody picked yet
    await click(button('handline.architect', reply())!);
    await typeInto($('textarea', reply()) as HTMLTextAreaElement, 'is the pane done?');
    await click(button('Send to handline.architect', reply())!);
    await until(
      () =>
        (s.daemon.chat as unknown as { messages: { to: string; body: string }[] }).messages.some(
          (m) => m.to === 'handline.architect' && m.body.includes('is the pane done?'),
        ),
      'the message to the architect',
    );
  });

  it('assigns the task: the picker groups who worked on it, then the note goes with it', async () => {
    const s = withVerbs(setup());
    await mount('#/tasks/1688', s);
    await until(() => button('Assign', $('.tverbs') ?? box), 'Assign');
    await click(button('Assign', $('.tverbs')!)!);
    const ask = sheet('Assign T1688 to…')!; // local-ref-ok
    expect($('section[aria-label="Worked on this task"]', ask)).not.toBeNull();
    await click(button('handline.architect', ask)!);
    await typeInto($('textarea', ask) as HTMLTextAreaElement, 'please take it');
    await click(button('Assign to handline.architect', ask)!);
    await until(() => s.daemon.tasks.get({ task: 1688 }).to === 'handline.architect', 'the new owner');
    await until(
      () =>
        (s.daemon.chat as unknown as { messages: { to: string; body: string }[] }).messages.some(
          (m) => m.to === 'handline.architect' && m.body.includes('please take it'),
        ),
      'the note',
    );
  });

  it('shows every action and state as a word', async () => {
    await mount('#/tasks/1688');
    await until(() => $('.tverbs'), 'the verbs');
    for (const b of $$('button', $('.tverbs')!)) expect(text(b).trim()).not.toBe('');
    expect(text($('.tdetail-head'))).toContain('doing');
    expect(text($('.tmeta'))).toContain('Owner');
  });

  it('serves the verbs of the daemon plan once it lists them: done needs a result, reopen only for a done task I asked for', async () => {
    const s = withVerbs(setup());
    await mount('#/tasks/1688', s);
    await until(() => $('.tverbs'), 'the verbs');
    await click(button('Done', $('.tverbs')!)!);
    const ask = sheet('done: what came of it?')!;
    await click(button('Done', ask)!);
    expect(text($('.tsheet-err'))).toContain('A reason is required.');
    await typeInto($('input', ask) as HTMLInputElement, 'landed as abc123');
    await click(button('Done', ask)!);
    await until(() => text($('.tdetail-head')).includes('done'), 'done');
    expect(s.daemon.tasks.get({ task: 1688 }).evidence).toBe('landed as abc123');
    // the founder's own done task: the key says why it cannot be reopened
    await act(async () => void (location.hash = '#/tasks/1650'));
    await until(() => text($('.tdetail-head')).includes('T1650'), 'task 1650'); // local-ref-ok
    expect(button('Reopen', $('.tverbs')!)).toBeUndefined(); // asked by 'founder', not by me
    await press('r', {}, $('.tbody')!);
    await until(() => text($('.toast')).includes('only a done task that you asked for can be reopened'), 'the bridge sentence');
  });
});

describe('actions on one task or many', () => {
  const shiftClick = (el: Element | null) =>
    act(async () => void el!.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })));
  const popupRow = (label: string) =>
    $$('.ui-popup [role="dialog"] button, .ui-popup button, .ui-popup [aria-disabled]').find((b) => text(b).startsWith(label));
  const menu = async (within: ParentNode, button_: string, rowLabel: string) => {
    await click(button(button_, within));
    await click(popupRow(rowLabel));
  };

  it('Shift-click marks the rows between, and "Select all" marks every row shown', async () => {
    await mount('#/tasks', withVerbs(setup()));
    await loaded();
    const ids = rowIds();
    await mark(ids[0]!);
    await shiftClick($(`input[aria-label="Mark T${ids[2]}"]`));
    expect($$('input[type="checkbox"]:checked').filter((c) => c.getAttribute('aria-label')?.startsWith('Mark T'))).toHaveLength(3);
    await click(button('Select all'));
    expect(text($('.ui-toolbar, [role="toolbar"]'))).toContain(`${ids.length} marked`);
  });

  it('runs Done on the marked tasks after a confirm with the count, and lists which failed and why', async () => {
    const s = withVerbs(setup());
    await mount('#/tasks', s);
    await loaded();
    await mark(1684);
    await mark(1688);
    s.daemon.knobs.refuse('task.done', 'state', 'task is locked', undefined, 1);
    await menu(box, 'More actions', 'Done');
    const sh = sheet('Done 2 tasks?')!;
    expect(sh).toBeTruthy();
    await typeInto($('input[aria-label="what came of it?"]', sh) as HTMLInputElement, 'checked by hand');
    await click(button('Done', sh));
    await until(() => sheet('done: 1 of 2 changed'), 'the result sheet');
    expect(text(sheet('done: 1 of 2 changed'))).toContain('task is locked');
    expect([1684, 1688].filter((id) => s.daemon.tasks.get({ task: id }).state === 'done')).toHaveLength(1);
  });

  it('skips what a verb cannot apply to, and says so in the confirm', async () => {
    const s = withVerbs(setup());
    await mount('#/tasks', s);
    await loaded();
    await mark(1690); // queued
    await mark(1700); // draft
    await menu(box, 'More actions', 'Queue');
    const sh = sheet('Queue 1 task?')!;
    expect(text(sh)).toContain('1 skipped');
    expect(text(sh)).toContain('1690 is not a draft');
    await click(button('Queue', sh));
    await until(() => s.daemon.tasks.get({ task: 1700 }).state === 'queued', 'the draft is queued');
  });

  it("a row's own menu releases a running task", async () => {
    const s = withVerbs(setup());
    await mount('#/tasks', s);
    await loaded();
    await menu(row(1684)!, 'Actions', 'Release');
    await click(button('Release', sheet('Release 1 task?')!));
    await until(() => s.daemon.tasks.get({ task: 1684 }).state === 'queued', 'released');
  });

  it('a verb the daemon does not serve is listed but cannot be used, and says why', async () => {
    await mount('#/tasks', setup());
    await loaded();
    await click(button('Actions', row(1684)!));
    const done = popupRow('Done')!;
    expect(done.getAttribute('aria-disabled')).toBe('true');
    expect(done.getAttribute('title')).toContain('does not serve task.done');
  });
});
