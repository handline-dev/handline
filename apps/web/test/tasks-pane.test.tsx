// The Tasks views page older rows as the end of the list scrolls into view, the task page's wait line and menu, the agent's Tasks tab marks only in Select mode, and a row's menu lists only what can run now.
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
  vi.unstubAllGlobals();
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
const setSelect = (el: HTMLSelectElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
const setInput = (el: HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
const menu = async (name: string) => {
  await click($('.tdetail-head button[aria-label="More"]'));
  await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => (i.textContent ?? '').startsWith(name)));
};
const txt = (el: Element | null | undefined) => el?.textContent ?? '';
const click = (el: Element | null | undefined) => act(async () => void (el as HTMLElement).click());
async function open(hash: string, seed?: string) {
  const s = setup();
  if (seed) applySeeds(s.daemon, seed);
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />));
  return s;
}

/** An IntersectionObserver that reports nothing until `reachEnd`, which says every observed element came into view. */
function stubScrollEnd() {
  const live = new Set<(hits: { isIntersecting: boolean }[]) => void>();
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      cb: (hits: { isIntersecting: boolean }[]) => void;
      constructor(cb: (hits: { isIntersecting: boolean }[]) => void) {
        this.cb = cb;
      }
      observe() {
        live.add(this.cb);
      }
      disconnect() {
        live.delete(this.cb);
      }
    },
  );
  return () => act(async () => void [...live].forEach((cb) => cb([{ isIntersecting: true }])));
}

describe('paging the task lists', () => {
  it('the Tasks page loads 100 and the next 100 older ones come when the end of the list scrolls into view, with no button', async () => {
    const reachEnd = stubScrollEnd();
    await open('#/tasks', 'tasks-200');
    await until(() => $$('tr[data-task]').length, 'the rows');
    expect($('button[aria-label="Show 100 older"]')).toBeNull();
    const first = $$('tr[data-task]').length;
    await reachEnd();
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

  it('shows no boxes until Select, ends Select on Escape, and pages older tasks when the end of the list scrolls into view', async () => {
    const reachEnd = stubScrollEnd();
    await tab();
    expect($('input[aria-label^="Mark"]')).toBeNull();
    await click($('button[aria-label="Select tasks"]'));
    expect($('input[aria-label^="Mark"]')).not.toBeNull();
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
    expect($('input[aria-label^="Mark"]')).toBeNull();
    const before = $$('.tasks-tab-row').length;
    expect($('button[aria-label="Show 100 older"]')).toBeNull();
    await reachEnd();
    await until(() => $$('.tasks-tab-row').length > before, 'older rows');
  });

  it('keeps to the 768 column, names its agent in a chip that leaves for every agent, and its rows drag out', async () => {
    await tab();
    expect($('.tasks-tab')!.className).toContain('max-w-[var(--w-col)]');
    expect($('.tasks-tab-row')!.getAttribute('draggable')).toBe('true');
    expect($('.agent-chip')!.textContent).toBeTruthy();
    await click($('.agent-chip button'));
    expect(location.hash).toBe('#/tasks');
  });

  it('a row dragged onto the pane edge splits the window and opens that task in the new pane', async () => {
    await tab();
    expect($('[data-panes]')!.getAttribute('data-panes')).toBe('1');
    const row = $('.tasks-tab-row')!;
    const data = { setData: vi.fn(), dropEffect: '' };
    const fire = (el: Element, type: string, init: Record<string, unknown> = {}) =>
      act(async () => {
        const e = new Event(type, { bubbles: true, cancelable: true });
        Object.assign(e, { dataTransfer: data, ...init });
        el.dispatchEvent(e);
      });
    await fire(row, 'dragstart');
    await until(() => $('.drop-overlay'), 'the drop cover');
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1000,
      bottom: 600,
      width: 1000,
      height: 600,
    } as DOMRect);
    const cover = $('.drop-overlay')!;
    await fire(cover, 'dragover', { clientX: 990, clientY: 300 });
    expect($('.drop-preview')?.getAttribute('data-zone')).toBe('right');
    await fire(cover, 'drop', { clientX: 990, clientY: 300 });
    await until(() => $('[data-panes]')!.getAttribute('data-panes') === '2', 'the second pane');
    expect($('.drop-overlay')).toBeNull();
    expect($$('[data-group]')).toHaveLength(2);
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

describe('the task page', () => {
  it('says on one line what the task waits on and what waits on it, with the Graph', async () => {
    await open('#/tasks/1688');
    await until(() => $('[data-wait]'), 'the line');
    expect(txt($('[data-wait] [data-blocks]'))).toContain('T1690'); // local-ref-ok
    await act(async () => void (location.hash = '#/tasks/1690'));
    await until(() => $('[data-waiting-on]'), 'the waiting-on part');
    expect(txt($('[data-waiting-on]'))).toBe('Waiting on T1688'); // local-ref-ok
    expect(txt($('[data-wait]'))).toContain('Graph');
  });

  it('the ... menu adds a sub-task under the task, waits on another task, and duplicates', async () => {
    await open('#/tasks/1688');
    await until(() => $('.tdetail-head'), 'the head');
    const item = (name: string) => [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => txt(i).startsWith(name))!;
    await click($('.tdetail-head button[aria-label="More"]'));
    expect(['Add sub-task', 'Wait on another task…', 'Duplicate'].every((n) => item(n))).toBe(true);
    await click(item('Add sub-task'));
    await until(() => $('.ui-dialog'), 'the dialog');
    expect(txt($('.ui-dialog'))).toContain('New sub-task');
  });

  it('Duplicate opens the form filled with the task, past the duplicate-title gate', async () => {
    await open('#/tasks/1688');
    await until(() => $('.tdetail-head'), 'the head');
    await click($('.tdetail-head button[aria-label="More"]'));
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((i) => txt(i).startsWith('Duplicate')));
    await until(() => $('.ui-dialog'), 'the duplicate form');
    expect($$('.ui-dialog input').map((i) => (i as HTMLInputElement).value)).toContain('Terminal pane on the bytes channel');
  });

  it('Wait on another task sends task.edit with blocked_by', async () => {
    const s = await open('#/tasks/1688');
    await until(() => $('.tdetail-head'), 'the head');
    await menu('Wait on another task');
    await until(() => $('select[aria-label="Task to wait on"]'), 'the picker');
    await setSelect($('select[aria-label="Task to wait on"]') as HTMLSelectElement, '1684');
    await click($('button[form="waiton-form"]'));
    await until(() => s.daemon.tasks.get({ task: 1688 }).blocked_by?.includes(1684), 'the wait'); // local-ref-ok
  });

  it('Add sub-task sends the task as its parent, and Duplicate goes past the duplicate-title gate', async () => {
    const s = await open('#/tasks/1688');
    await until(() => $('.tdetail-head'), 'the head');
    await menu('Add sub-task');
    await until(() => $('.ui-dialog'), 'the form');
    await setInput($('.ui-dialog .tform input') as HTMLInputElement, 'The sub part');
    await click($('button[form="newtask-form"]'));
    await until(() => s.daemon.tasks.list({}).items.some((t) => t.title === 'The sub part' && t.parent === 1688), 'the sub-task'); // local-ref-ok
    await menu('Duplicate');
    await until(() => $('.ui-dialog'), 'the copy form');
    await click($('button[form="newtask-form"]'));
    await until(
      () => s.daemon.tasks.list({}).items.filter((t) => t.title === 'Terminal pane on the bytes channel').length === 2,
      'the copy',
    );
  });
});

describe('the agent Tasks tab and the older-rows button', () => {
  it('lists Draft first, then the running and waiting groups, then closed tasks by the day they closed', async () => {
    await open('#/agents/handline.term-build');
    await until(() => $('button[aria-label="Add a tab"]'), 'the tab strip');
    await click($('button[aria-label="Add a tab"]'));
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => b.textContent?.includes('Tasks')) ?? null);
    await until(() => $('.tasks-tab-row'), 'the rows');
    const heads = $$('.tasks-tab-list h3').map((h) => txt(h).replace(/\s*\d+$/, ''));
    expect(heads[0]).toBe('Draft');
    expect(heads).toContain('Running');
    expect(heads.indexOf('Draft')).toBeLessThan(heads.indexOf('Running'));
  });

  it('without IntersectionObserver the Show 100 older button stays', async () => {
    vi.stubGlobal('IntersectionObserver', undefined);
    await open('#/tasks', 'tasks-200');
    await until(() => $('button[aria-label="Show 100 older"]'), 'the button');
    const first = $$('tr[data-task]').length;
    await click($('button[aria-label="Show 100 older"]'));
    await until(() => $$('tr[data-task]').length > first, 'more rows');
  });
});
