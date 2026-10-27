import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  Store,
  activate,
  applyPreset,
  closeTab,
  defaultWorkspace,
  openTab,
  saveWorkspace,
  splitTab,
  storageKey,
  type KV,
} from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect'; // idle with a session
const TB = 'handline.term-build'; // parked, no session
const wait = (ms = 10) =>
  act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
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
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const fire = (el: Element, type: string) =>
  act(async () => {
    el.dispatchEvent(new Event(type, { bubbles: true, cancelable: true }));
    if (type === 'dragstart') await new Promise((r) => setTimeout(r, 0)); // the drop cover mounts after the dragstart event
  });
const rect = (l: number, t: number, w: number, h: number) =>
  ({ left: l, top: t, width: w, height: h, right: l + w, bottom: t + h, x: l, y: t, toJSON() {} }) as DOMRect;
/** jsdom has no layout: every pane is 800 x 600 with a 32 px strip of 100 px tabs. */
const stubRects = () =>
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.matches('.pane, .drop-overlay')) return rect(0, 0, 800, 600);
    if (this.matches('.strip')) return rect(0, 0, 800, 32);
    if (this.matches('[role=tab]')) return rect([...this.parentElement!.querySelectorAll('[role=tab]')].indexOf(this) * 100, 0, 100, 32);
    return rect(0, 0, 0, 0);
  });
/** A drag event over a pane's cover at a point of the pane. */
const dragAt = (pane: Element, type: 'dragover' | 'drop', x: number, y: number) =>
  act(async () => {
    const e = Object.assign(new Event(type, { bubbles: true, cancelable: true }), {
      clientX: x,
      clientY: y,
      dataTransfer: { dropEffect: '' },
    });
    pane.querySelector('.drop-overlay')!.dispatchEvent(e);
  });
const click = (el: Element) =>
  act(async () => {
    (el as HTMLElement).click();
  });
const text = (el: Element | null) => el?.textContent ?? '';
// The row's context menu is Radix's, in a portal: it opens on the row's contextmenu event.
const menuItems = () => [...document.querySelectorAll('[role=menuitem]')].map((b) => text(b));
const termTab = `[data-tab="terminal:term:${ARCH}"]`;

// The text grid is what these tests drive; the xterm pane has its own file.
const textGrid = (): KV & { data: Map<string, string> } => {
  const m = memory();
  m.setItem('terminal', 'text');
  return m;
};

/** The old default, Chat beside the terminal in two panes, for the tests that drive both. `fresh` leaves the real default. */
const sideBySide = (agent: string, storage: KV) => {
  const key = storageKey('test', agent);
  if (storage.getItem(key)) return;
  const w = defaultWorkspace({ terminal: `term:${agent}` });
  saveWorkspace(storage, key, splitTab(w, `terminal:term:${agent}`, 'g1', 'right')!);
};

async function mount(agent: string, storage: KV = textGrid(), s = setup(), fresh = false) {
  if (!fresh) sideBySide(agent, storage);
  const store = new Store(s.client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={storage} />);
  });
  return { store, storage, ...s };
}
const unmount = async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
};

beforeEach(() => {
  history.replaceState(null, '');
  vi.stubGlobal('confirm', () => true);
});
// The fixtures are shared between tests: the auditor is at its usage wall (blocked, not working), and what a test changes is put back after it.
const restores: (() => void)[] = [];
function auditorWorking(daemon: ReturnType<typeof setup>['daemon']) {
  const a = daemon.sessions.get('handline.auditor')!;
  const was = { state: a.state, wall: a.fx.wall };
  restores.push(
    () => Object.assign(a, { state: was.state }),
    () => void (a.fx.wall = was.wall),
  );
  a.state = 'running';
  a.fx.wall = false;
}
afterEach(async () => {
  restores.splice(0).forEach((r) => r());
  await unmount();
  vi.unstubAllGlobals();
});

describe('the workspace', () => {
  it('has no agent header and no per-agent tab row; every pane has its own strip, Chat first and unclosable', async () => {
    await mount(ARCH);
    await until(() => $$('.pane').length === 2, 'two panes');
    expect($('.agent-head')).toBeNull();
    expect($('.tabstrip')).toBeNull();
    expect($('.topbar')).toBeNull();
    for (const p of $$('.pane')) {
      expect(p.querySelectorAll('.strip')).toHaveLength(1);
      expect(p.firstElementChild!.classList.contains('strip')).toBe(true);
    }
    expect($$('[data-tab="chat"]')).toHaveLength(1);
    expect($('[data-tab="chat"] .x')).toBeNull();
    expect(text($('[data-tab="chat"]'))).toBe('Chat');
    expect($(`${termTab} .x`)).not.toBeNull();
    expect($('.chat .compose')).not.toBeNull(); // the Chat tab: its own tests are in chat.test.tsx
    await until(() => text($('.grid')).includes('Claude Code v2.1'), 'terminal text');
  });

  it('has one window row, nav rows with icons, the foot icons and a status bar along the bottom (G10 to G12)', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    expect($('.side')).toBeNull();
    const top = $$('.rail-top button').map((b) => b.getAttribute('aria-label') ?? '');
    expect(top.slice(0, 3)).toEqual(['Hide the agent list (⌘B)', 'Back (⌘[)', 'Forward (⌘])']);
    expect(text($('.rail-nav'))).toMatch(/My turn.*Tasks.*Requests.*Usage/);
    expect($$('.rail-nav .nav svg').length).toBe(4);
    expect(text($('.rail-nav'))).not.toMatch(/More|Team|Worktrees/);
    expect($$('.rail-foot button').map((b) => b.getAttribute('aria-label'))).toEqual(['Settings', 'Keys (?)']);
    expect($('.rail-status')).toBeNull();
    const bar = $('.statusbar')!;
    expect($('#app')!.parentElement).toBe($('.frame'));
    expect(bar.querySelector('[aria-label="daemon open"]')).not.toBeNull();
    expect(text(bar)).not.toMatch(/offline|daemon ok|need you|blocked/); // up, the daemon is a mark with no word; the counts are not here
    expect($('.rail input[type=search]')).toBeNull(); // the palette (⌘K) finds agents
  });

  it('the list is under team headings with the task under each name, and the view menu switches the groups off', async () => {
    await mount(ARCH);
    await until(() => $('.rail-list [data-agent]'), 'the rows');
    expect($$('.team-head').length).toBeGreaterThan(0);
    expect(text($('.team-head'))).toMatch(/^\w+$/);
    expect($$('.row-line').some((r) => text(r).includes('Web UI design'))).toBe(true);
    for (const r of $$('.rail-list .row')) {
      expect(r.querySelectorAll('.row-line')).toHaveLength(1); // name and one line, so every row is as tall as the next
      expect(r.className).toContain('h-11');
      expect(text(r.querySelector('.row-line'))).not.toMatch(/no terminal|idle/i);
    }
    await click($('.rail-opts [aria-label^="View"]')!);
    await click([...document.querySelectorAll<HTMLElement>('[role=menuitemcheckbox]')].find((b) => text(b).includes('Group by team'))!);
    expect($$('.team-head')).toHaveLength(0);
  });

  it('the nav rows go to their pages, Back and Forward use the history, and ⌘B hides and shows the sidebar', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    await click($$('.rail-nav .nav').find((b) => text(b).trim().startsWith('My turn'))!);
    expect(location.hash).toBe('#/my-turn');
    await click($$('.rail-nav .nav').find((b) => text(b).trim().startsWith('Tasks'))!);
    expect(location.hash).toMatch(/^#\/tasks/);
    const back = vi.spyOn(history, 'back').mockImplementation(() => undefined);
    const forward = vi.spyOn(history, 'forward').mockImplementation(() => undefined);
    await click($('.rail-top [aria-label="Back (⌘[)"]')!);
    await click($('.rail-top [aria-label="Forward (⌘])"]')!);
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: '[', metaKey: true })));
    expect(back).toHaveBeenCalledTimes(2);
    expect(forward).toHaveBeenCalledTimes(1);
    back.mockRestore();
    forward.mockRestore();
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'b', metaKey: true })));
    expect($('.rail')).toBeNull();
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'b', ctrlKey: true })));
    expect($('.rail')).not.toBeNull();
  });

  it('⌘K opens the palette: pages first, agents and tasks by what is typed; Enter goes there and Escape closes', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })));
    const field = $('.palette input') as HTMLInputElement;
    expect($$('.palette [role="option"]').map((o) => text(o))).toEqual(
      expect.arrayContaining([expect.stringContaining('My turn'), expect.stringContaining('Tasks')]),
    );
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, 'auditor');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await until(() => $$('.palette [role="option"]').some((o) => text(o).includes('handline.auditor')), 'the agent hit');
    await act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect($('.palette')).toBeNull();
    expect(location.hash).toBe('#/agents/handline.auditor');
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true })));
    await act(async () => void $('.palette input')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect($('.palette')).toBeNull();
  });

  it('? lists the keys on a page without its own, and the foot Keys icon does the same', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: '?' })));
    expect($('[role="dialog"][aria-label="Keys"]')).not.toBeNull();
    expect(text($('.keys-list'))).toContain('⌘K');
    expect(text($('[role="dialog"][aria-label="Keys"]'))).toContain('In the message box'); // the open chat registered its keys
    expect(text($('[role="dialog"][aria-label="Keys"]'))).toContain('send the message');
    expect(($('[role="dialog"][aria-label="Keys"]')!.textContent ?? '').split('On the tabs')).toHaveLength(2); // once, not once per pane
    await act(async () => void document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))); // Base UI listens on the document
    expect($('[role="dialog"][aria-label="Keys"]')).toBeNull();
    await click($('.rail-foot [aria-label="Keys (?)"]')!);
    expect($('[role="dialog"][aria-label="Keys"]')).not.toBeNull();
  });

  it('opens with Chat alone in view and the Claude Code tab second, behind it; closing a running one asks first', async () => {
    const { daemon } = await mount(ARCH, textGrid(), setup(), true);
    await until(() => $$('.pane').length === 1 && $(termTab), 'one pane with the terminal tab');
    expect($$('.pane [data-tab]').map((t) => t.getAttribute('data-tab'))).toEqual(['chat', `terminal:term:${ARCH}`]);
    expect($('[data-tab="chat"]')!.getAttribute('aria-selected')).toBe('true');
    expect($('.terminal-tab')).toBeNull(); // not shown
    expect($('[data-tab="chat"] .x')).toBeNull(); // Chat is pinned: it has no close
    vi.stubGlobal('confirm', () => false);
    await click($(`${termTab} .x`)!);
    expect($(termTab)).not.toBeNull(); // refused at the confirm
    vi.stubGlobal('confirm', () => true);
    await click($(`${termTab} .x`)!);
    expect($(termTab)).toBeNull();
    expect(daemon.sessions.get(ARCH)!.state).not.toBe('closed');
  });

  it('showing a tab is a history entry, and an entry coming back puts its tab back', async () => {
    await mount(ARCH, textGrid(), setup(), true);
    await until(() => $(termTab), 'the terminal tab');
    const term = `terminal:term:${ARCH}`;
    const shown = () => ($(termTab)!.getAttribute('aria-selected') === 'true' ? term : 'chat');
    const length = history.length;
    await click($(termTab)!);
    expect(shown()).toBe(term);
    await click($('[data-tab="chat"]')!);
    expect(shown()).toBe('chat');
    expect(history.length).toBe(length + 2); // one entry per move
    // jsdom does not walk the history, so the walk is played: an entry's state comes back and popstate fires.
    const walk = async (state: object) => {
      history.replaceState(state, '');
      await act(async () => void dispatchEvent(new PopStateEvent('popstate')));
    };
    const atChat = history.state as object;
    await click($(termTab)!);
    const atTerm = history.state as object;
    await walk(atChat);
    expect(shown()).toBe('chat');
    await walk(atTerm);
    expect(shown()).toBe(term);
  });

  it('a pane split is a history entry, and Back puts the single pane back', async () => {
    await mount(ARCH, textGrid(), setup(), true);
    await until(() => $(termTab), 'the terminal tab');
    const before = history.state as object;
    const length = history.length;
    const rects = stubRects();
    await fire($(termTab)!, 'dragstart');
    await dragAt($('.pane')!, 'drop', 790, 300);
    rects.mockRestore();
    expect($$('.pane')).toHaveLength(2);
    expect(history.length).toBe(length + 1);
    history.replaceState(before, '');
    await act(async () => void dispatchEvent(new PopStateEvent('popstate')));
    expect($$('.pane')).toHaveLength(1);
  });

  it('Back onto a closed tab opens it again', async () => {
    await mount(ARCH, textGrid(), setup(), true);
    await until(() => $(termTab), 'the terminal tab');
    const withTerm = history.state as object;
    vi.stubGlobal('confirm', () => true);
    await click($(`${termTab} .x`)!);
    expect($(termTab)).toBeNull();
    history.replaceState(withTerm, '');
    await act(async () => void dispatchEvent(new PopStateEvent('popstate')));
    expect($(termTab)).not.toBeNull();
  });

  it('the sidebar is resized by dragging its edge, clamped, kept, and reset by a double click', async () => {
    const storage = textGrid();
    await mount(ARCH, storage);
    await until(() => $('.rail-divider'), 'the divider');
    const rail = () => ($('#app') as HTMLElement).style.getPropertyValue('--rail-w');
    expect(rail()).toBe('280px');
    await act(async () => {
      $('.rail-divider')!.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, buttons: 1, clientX: 360 }));
    });
    expect(rail()).toBe('360px');
    expect(storage.getItem('railWidth')).toBe('360');
    await act(async () => {
      $('.rail-divider')!.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, buttons: 1, clientX: 5000 }));
    });
    expect(rail()).toBe('560px');
    await unmount();
    await mount(ARCH, storage);
    await until(() => $('.rail-divider'), 'the divider again');
    expect(rail()).toBe('560px');
    await act(async () => void $('.rail-divider')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })));
    expect(rail()).toBe('280px');
  });

  it("closing a tab only detaches; closing a pane's last tab removes the pane; Chat stays", async () => {
    const { daemon } = await mount(ARCH);
    await until(() => $$('.pane').length === 2, 'two panes');
    await click($(`${termTab} .x`)!);
    expect($$('.pane')).toHaveLength(1);
    expect($('[data-tab="chat"]')).not.toBeNull();
    expect(daemon.sessions.get(ARCH)!.state).not.toBe('closed'); // the agent keeps running
    expect($('[data-tab="chat"] .x')).toBeNull();
  });

  it('a tab dropped on a pane edge splits it; the same thing opened twice is focused', async () => {
    await mount(ARCH);
    await until(() => $$('.pane').length === 2, 'two panes');
    const rects = stubRects();
    await fire($(termTab)!, 'dragstart');
    const g1 = $('.pane[data-group="g1"]')!;
    await dragAt(g1, 'dragover', 400, 590);
    const preview = g1.querySelector<HTMLElement>('.drop-preview')!;
    expect(preview.dataset.zone).toBe('bottom');
    expect([preview.style.left, preview.style.top, preview.style.width, preview.style.height]).toEqual(['0px', '300px', '800px', '300px']); // the half the split takes
    await dragAt(g1, 'drop', 400, 590);
    rects.mockRestore();
    expect($('.split-col')).not.toBeNull();
    expect($$('.pane')).toHaveLength(2);
    expect($('.drop-overlay')).toBeNull(); // the drag ended
    await click($('.pane[data-group="g1"] [aria-label="Add a tab"]')!);
    expect([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].map((b) => text(b).trim())).not.toContain('Claude Code');
    expect($$('[data-tab^="terminal:"]')).toHaveLength(1);
  });

  it("the + menu's Terminal opens a new shell of the agent, beside its Claude Code tab, and closing the tab ends the shell", async () => {
    const s = setup();
    await mount(ARCH, textGrid(), s);
    await until(() => $(termTab), 'the Claude Code tab');
    await click($('[aria-label="Add a tab"]')!);
    await click([...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find((b) => text(b).trim() === 'Terminal')!);
    await until(() => $('[data-tab^="shell:"]'), 'the shell tab');
    expect([...s.daemon.script.shells]).toHaveLength(1);
    expect($(termTab)).not.toBeNull(); // the agent's own terminal is untouched
    await click($('[data-tab^="shell:"] .x')!);
    await until(() => s.daemon.script.shells.size === 0, 'the shell closed');
    expect($('[data-tab^="shell:"]')).toBeNull();
  });

  it('allows four panes, and a lone tab dragged to another pane edge frees its own pane so the count holds', async () => {
    const storage = memory();
    let ws = closeTab(defaultWorkspace({ terminal: `term:${ARCH}` }), 'activity');
    ws = applyPreset(openTab(openTab(ws, 'markdown', { path: 'a.md' }), 'web', { port: 3000 }), 'grid'); // 2 x 2: four columns would be under the minimum pane width
    saveWorkspace(storage, storageKey('test', ARCH), ws);
    await mount(ARCH, storage);
    await until(() => $$('.pane').length === 4, 'four panes');
    expect(text($('[data-tab="markdown:a.md"]'))).toContain('markdown');
    expect(box.textContent).toMatch(/does not serve markdown tabs/); // a kind this daemon lacks stays visible, closable

    const rects = stubRects();
    await fire($(termTab)!, 'dragstart');
    await dragAt($('.pane[data-group="g1"]')!, 'drop', 790, 300); // the terminal is alone in its pane: it frees one, so this is allowed
    rects.mockRestore();
    expect($$('.pane')).toHaveLength(4);
    expect($('.toast')).toBeNull();
  });

  it('a drop on the centre merges into the pane, a drop on a strip inserts at the line, and the Chat tab cannot be dragged', async () => {
    const storage = memory();
    let ws = defaultWorkspace({ terminal: `term:${ARCH}` });
    ws = applyPreset(openTab(openTab(ws, 'markdown', { path: 'a.md' }), 'web', { port: 3000 }), 'grid');
    ws = openTab(ws, 'shell', {}, 'g1'); // a second tab in the first pane
    saveWorkspace(storage, storageKey('test', ARCH), ws);
    await mount(ARCH, storage);
    await until(() => $$('.pane').length === 4, 'four panes');
    expect($('[data-tab="chat"]')!.getAttribute('draggable')).toBe('false');
    const rects = stubRects();
    const last = $$('.pane')[3]!;
    await fire($('[data-tab="shell:1"]')!, 'dragstart');
    await dragAt(last, 'dragover', 400, 300);
    expect(last.querySelector<HTMLElement>('.drop-preview')!.dataset.zone).toBe('center');
    await dragAt(last, 'dragover', 50, 10);
    expect(last.querySelector('.drop-line')).not.toBeNull(); // the insert line between tabs
    await dragAt(last, 'drop', 50, 10);
    rects.mockRestore();
    expect($$('.pane')).toHaveLength(4);
    expect(last.querySelector('[data-tab="shell:1"]')).not.toBeNull();
  });

  it('restores the layout after a reload, and a closed tab stays closed', async () => {
    const storage = memory();
    await mount(ARCH, storage);
    await until(() => $$('.pane').length === 2, 'two panes');
    const rects = stubRects();
    await fire($(termTab)!, 'dragstart');
    await dragAt($('.pane[data-group="g1"]')!, 'drop', 400, 590);
    rects.mockRestore();
    expect(storage.data.has(storageKey('test', ARCH))).toBe(true);
    await unmount();
    await mount(ARCH, storage);
    await until(() => $$('.pane').length === 2, 'two panes after reload');
    expect($('.split-col')).not.toBeNull();
    await click($(`${termTab} .x`)!);
    await unmount();
    await mount(ARCH, storage);
    await until(() => $('[data-tab="chat"]'), 'chat after reload');
    expect($$('.pane')).toHaveLength(1);
    expect($(termTab)).toBeNull();
  });
});

describe('the sidebar', () => {
  it('a lead row with sub-agents working says nothing while it is open', async () => {
    const s = setup();
    auditorWorking(s.daemon);
    await mount(TB, undefined, s);
    await until(() => $$('.row-wrap').length === 3, 'rows');
    expect($('.row-working')).toBeNull();
  });

  it('a pinned lead row counts the sub-agents working and the ones that cannot go on, its tree open or not', async () => {
    const s = setup();
    auditorWorking(s.daemon);
    const b = s.daemon.sessions.get('handline.term-build')!;
    const was = b.fx.wall;
    restores.push(() => void (b.fx.wall = was));
    b.fx.wall = true; // walled like the auditor is until it works
    await mount(TB, undefined, s);
    await until(() => $$('.row-wrap').length === 3, 'rows');
    const lead = $$('.row-wrap').find((r) => r.querySelector('.kids, [aria-expanded]'))!;
    const name = lead.closest('[data-agent]')!.getAttribute('data-agent')!;
    await act(async () => void lead.dispatchEvent(new PointerEvent('pointerover', { bubbles: true })));
    await click(lead.querySelector('[aria-haspopup="menu"]')!);
    await until(() => menuItems().length, 'the row menu');
    await click([...document.querySelectorAll<HTMLElement>('[role=menuitem]')].find((i) => text(i).trim() === 'Pin')!);
    await until(() => $('.pinned-head'), 'the pinned section');
    const pinned = $$('.row-working').filter((w) => w.closest('[data-agent]')?.getAttribute('data-agent') === name);
    expect(pinned.map((w) => text(w).trim())).toEqual(['1']); // the pinned copy; the open tree row says nothing
    expect(pinned[0]!.querySelector('svg')!.classList.contains('animate-spin')).toBe(true); // the count's ring turns like the state glyph
    const stuck = $$('.row-stuck').filter((w) => w.closest('[data-agent]')?.getAttribute('data-agent') === name);
    expect(stuck.map((w) => text(w).trim())).toEqual(['1']);
  });

  it("a row's menu holds Settings, which opens that agent's settings, whose back button, above the title, returns to the agent", async () => {
    await mount(TB);
    await until(() => $$('.row-wrap').length === 3, 'rows');
    const row = $('[data-agent="handline.architect"]')!;
    await act(async () => void row.dispatchEvent(new PointerEvent('pointerover', { bubbles: true })));
    expect(row.querySelector('[aria-label="Settings of handline.architect"]')).toBeNull(); // no separate gear on the row
    await click(row.querySelector('[aria-haspopup="menu"]')!);
    await until(() => menuItems().length, 'the row menu');
    await click([...document.querySelectorAll<HTMLElement>('[role=menuitem]')].find((i) => text(i).trim() === 'Settings')!);
    expect(location.hash).toBe('#/agents/handline.architect/settings');
    await until(() => $('.settings-agent .back'), 'the back button');
    expect($('.settings-agent .back')!.nextElementSibling).toBeNull(); // on a line of its own, apart from the title
    await click($('.settings-agent .back')!);
    expect(location.hash).toBe('#/agents/handline.architect');
  });

  it('shows no Start on a row, and the row menu keeps only Settings, Open in, the copies, what stops a turn and Delete agent', async () => {
    await mount(TB);
    await until(() => $$('.row-wrap').length === 3, 'rows');
    expect($('.row-actions')).toBeNull(); // no ⋮ on any row until one is hovered, selected or not
    await act(async () => void $('.row-wrap')!.dispatchEvent(new PointerEvent('pointerover', { bubbles: true })));
    expect($('.row-actions')!.parentElement!.className).not.toContain('absolute'); // it takes its own space, so the time and the line beside it are not covered
    expect($$('.row-actions .ui-btn').some((b) => /Resume|Start/.test(text(b)))).toBe(false);
    await click($('.row-actions [aria-haspopup="menu"]')!);
    await until(() => menuItems().length, 'the row menu');
    expect(menuItems()).toEqual(['Pin', 'Settings', 'Copy path', 'Copy worktree name', 'Delete agent']);
  });

  it('keeps the selected row short (no model, account or detail block there)', async () => {
    await mount(ARCH);
    await until(() => $('.row-wrap.on'), 'the selected row');
    expect($('.row-detail')).toBeNull();
    const r = text($('.row-wrap.on'));
    expect(r).not.toMatch(/opus|alpha|yourname/);
    expect($('.row-wrap.on .name')?.textContent).toBe('architect'); // the team prefix is the group's title
    await until(() => $('.terminal-tab'), 'terminal tab');
    expect($('.row-actions')).toBeNull(); // selected is not hovered: no ⋮
    expect($('.row-actions .ui-btn.solid')).toBeNull();
  });

  it("switching agent opens that agent's own workspace, with its own terminal tab", async () => {
    await mount(ARCH);
    await until(() => $(termTab), 'architect terminal tab');
    await act(async () => {
      location.hash = `#/agents/${TB}`;
      dispatchEvent(new Event('hashchange'));
    });
    await until(() => $('[data-tab="terminal:term:handline.term-build"]'), 'term-build terminal tab');
    expect($(termTab)).toBeNull();
    expect($$('[data-tab="chat"]')).toHaveLength(1);
  });
});

describe('badges', () => {
  it('say a stall in the rail, and a dialog on the rail row and on the terminal tab, with a dot while that tab is hidden', async () => {
    const storage = textGrid();
    const ws = activate(applyPreset(defaultWorkspace({ terminal: `term:${ARCH}` }), 'single'), 'chat'); // one pane, Chat showing
    saveWorkspace(storage, storageKey('test', ARCH), ws);
    const m = await mount(ARCH, storage);
    await until(() => $(termTab), 'the terminal tab');
    expect($('[data-agent="handline.auditor"] .badge.stalled')?.title).toMatch(/^wall since/);
    expect($(`${termTab} .badge`)).toBeNull();
    await m.store.client.sendInput(ARCH, 'push it');
    m.daemon.settle(); // the scripted turn ends at a permission dialog
    await until(() => $(`${termTab} .badge.dialog`), 'the dialog badge on the tab');
    expect($(`[data-agent="${ARCH}"] .badge.dialog`)).not.toBeNull();
    expect($(`${termTab} .unread-dot`)).not.toBeNull(); // the terminal is behind Chat
    await click($(termTab)!);
    expect($(`${termTab} .unread-dot`)).toBeNull(); // showing it clears the dot
  });
});

describe('the daemon-down notice', () => {
  it('says the daemon is not running while the connection is lost, and goes when it is back', async () => {
    const m = await mount(ARCH);
    await until(() => $('.main'), 'the page');
    expect($('.daemon-down')).toBeNull();
    m.net.down = true;
    m.daemon.dropConnections();
    await until(() => $('.daemon-down'), 'the notice');
    expect(text($('.daemon-down'))).toContain("Handline isn't running");
    expect(text($('.daemon-down code'))).toBe('hl service install'); // visible without the clipboard (a page over plain http has none)
    expect($$('.daemon-down button').some((b) => text(b) === 'Copy command')).toBe(true);
    m.net.down = false;
    await until(() => !$('.daemon-down'), 'the notice gone');
  });
});
