// The Tasks views page by 100 on a button, the agent's Tasks tab marks only in Select mode, and a row's menu lists only what can run now.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { applySeeds } from '@handline/fake-daemon/seeds';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let box: HTMLElement;
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.restoreAllMocks();
});

const wait = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
const until = async (f: () => unknown, what: string) => {
  for (let i = 0; i < 300; i++) {
    if (f()) return;
    await wait(10);
  }
  throw new Error(`never: ${what}`);
};
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element | null) => act(async () => void (el as HTMLElement).click());
async function open(hash: string, seed?: string) {
  const s = setup();
  if (seed) applySeeds(s.daemon, seed);
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
  return s;
}

describe('paging the task lists', () => {
  it('the Tasks page loads 100 and a button brings the next 100 older ones', async () => {
    await open('#/tasks', 'tasks-200');
    await until(() => $('button[aria-label="Show 100 older"]'), 'the older button');
    const first = $$('tr[data-task]').length;
    await click($('button[aria-label="Show 100 older"]'));
    await until(() => $$('tr[data-task]').length > first, 'more rows');
    expect($$('tr[data-task]').length).toBeGreaterThan(first);
  });
});

describe("an agent's Tasks tab", () => {
  const tab = async () => {
    await open('#/agents/handline.architect', 'tasks-200');
    await until(() => $('button[aria-label="Add a tab"]'), 'the tab strip');
    await click($('button[aria-label="Add a tab"]'));
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => b.textContent?.includes('Tasks')) ?? null);
    await until(() => $('.tasks-tab-row'), 'the rows');
  };

  it('shows no boxes until Select, ends Select on Escape, and pages older tasks on a button', async () => {
    await tab();
    expect($('input[aria-label^="Mark"]')).toBeNull();
    await click($('button[aria-label="Select tasks"]'));
    expect($('input[aria-label^="Mark"]')).not.toBeNull();
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect($('input[aria-label^="Mark"]')).toBeNull();
    const before = $$('.tasks-tab-row').length;
    await click($('button[aria-label="Show 100 older"]'));
    await until(() => $$('.tasks-tab-row').length > before, 'older rows');
  });

  it("a row's menu leaves out what cannot run and puts the stopping verbs last", async () => {
    await tab();
    await click($('.tasks-tab-row')!.parentElement!.querySelector('button[aria-label="Actions"]'));
    const items = [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].map((i) => i.textContent ?? '');
    expect(items.length).toBeGreaterThan(0);
    expect(items.some((t) => t.startsWith('Reopen'))).toBe(false); // an open task has nothing to reopen
    const stop = items.findIndex((t) => /^(Withdraw|Cancel|Discard)/.test(t));
    const edit = items.findIndex((t) => t.startsWith('Assign to'));
    expect(stop).toBeGreaterThan(edit);
  });
});

describe("the graph in an agent's Tasks tab", () => {
  it("shows only that agent's column, with the same cards", async () => {
    await open('#/agents/handline.architect', 'tasks-200');
    await until(() => $('.tasks-tab-list'), 'the list');
    await click($('[aria-label="View"] [aria-label="Graph"]'));
    await until(() => $('[data-testid="task-graph"]'), 'the graph');
    expect($$('[data-testid="task-graph"] h2').map((h) => h.textContent)).toEqual(['architect']);
    expect($('[data-testid="task-graph"] [data-node]')).not.toBeNull();
    expect($('.tasks-tab-list')).toBeNull();
  });
});

describe('a narrow Tasks list (detail panel open)', () => {
  it('lets the title take the free width and the group headings span exactly the columns shown', async () => {
    await open('#/tasks', 'tasks-200');
    await until(() => $('tr[data-task]'), 'a row');
    const cls = (sel: string) => $(sel)!.className;
    expect(cls('tr[data-task] .c-title')).toContain('min-w-0');
    expect($('tr[data-task] .ttitle')!.className).toContain('line-clamp-2');
    expect(cls('tr[data-task] .c-time')).toContain('whitespace-nowrap');
    expect($('tr[data-task] .c-owner span')!.getAttribute('title')).toMatch(/^assigned to: /);
    expect($('.tgroup th')!.getAttribute('colspan')).toBe(String($('tr[data-task]')!.children.length));
  });

  it.each([
    [500, 5],
    [650, 6],
    [800, 7],
  ])('at a list %ipx wide the table has %i columns, the colgroup, the rows and the headings alike', async (width, cols) => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('tasks-scroll') ? width : 0;
    });
    await open('#/tasks', 'tasks-200');
    await until(() => $('tr[data-task]'), 'a row');
    await until(() => $$('.ttable col').length === cols, 'the columns');
    expect($('tr[data-task]')!.children.length).toBe(cols);
    expect($('.tgroup th')!.getAttribute('colspan')).toBe(String(cols));
  });
});
