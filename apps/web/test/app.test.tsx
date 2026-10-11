import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, activate, applyPreset, defaultWorkspace, openTab, saveWorkspace, splitTab, storageKey, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

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
  });
const click = (el: Element) =>
  act(async () => {
    (el as HTMLElement).click();
  });
const text = (el: Element | null) => el?.textContent ?? '';
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
  vi.stubGlobal('confirm', () => true);
});
afterEach(async () => {
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
    expect(text($('.rail-nav'))).toMatch(/Inbox.*Tasks.*Team.*Worktrees.*Accounts.*Stats/);
    expect($$('.rail-nav .nav svg').length).toBe(6);
    expect($$('.rail-foot button').map((b) => b.getAttribute('aria-label'))).toEqual(['Settings', 'Keys (?)']);
    expect($('.rail-status')).toBeNull();
    const bar = $('.statusbar')!;
    expect($('#app')!.parentElement).toBe($('.frame'));
    expect(bar.querySelector('[aria-label="daemon open"]')).not.toBeNull();
    expect(bar.querySelector('[data-icon="agent"]')).not.toBeNull();
    expect($('.rail input[type=search]')).not.toBeNull();
  });

  it('the nav rows go to their pages, Back and Forward use the history, and ⌘B hides and shows the sidebar', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    const nav = (label: string) => $$('.rail-nav .nav').find((b) => text(b).includes(label))!;
    await click(nav('Stats'));
    expect(location.hash).toBe('#/stats');
    await click(nav('Accounts'));
    expect(location.hash).toBe('#/accounts');
    expect(nav('Accounts').classList.contains('on')).toBe(true);
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
      expect.arrayContaining([expect.stringContaining('Inbox'), expect.stringContaining('Tasks')]),
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
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })));
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
    vi.stubGlobal('confirm', () => false);
    await click($(`${termTab} .x`)!);
    expect($(termTab)).not.toBeNull(); // refused at the confirm
    vi.stubGlobal('confirm', () => true);
    await click($(`${termTab} .x`)!);
    expect($(termTab)).toBeNull();
    expect(daemon.sessions.get(ARCH)!.state).not.toBe('closed');
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
    await fire($(termTab)!, 'dragstart');
    expect($$('.dz').length).toBeGreaterThan(0);
    await fire($('.pane[data-group="g1"] .dz.bottom')!, 'drop');
    expect($('.split-col')).not.toBeNull();
    expect($$('.pane')).toHaveLength(2);
    expect($('.dz')).toBeNull(); // the drag ended
    await click($('.pane[data-group="g1"] [aria-label="Add a tab"]')!);
    await click($$('.pane[data-group="g1"] .menu-item').find((b) => text(b).trim() === 'Claude Code')!);
    expect($$('[data-tab^="terminal:"]')).toHaveLength(1);
  });

  it('allows four panes, and a lone tab dragged to another pane edge frees its own pane so the count holds', async () => {
    const storage = memory();
    let ws = defaultWorkspace({ terminal: `term:${ARCH}` });
    ws = applyPreset(openTab(openTab(ws, 'markdown', { path: 'a.md' }), 'web', { port: 3000 }), 'grid'); // 2 x 2: four columns would be under the minimum pane width
    saveWorkspace(storage, storageKey('test', ARCH), ws);
    await mount(ARCH, storage);
    await until(() => $$('.pane').length === 4, 'four panes');
    expect(text($('[data-tab="markdown:a.md"]'))).toContain('markdown');
    expect(box.textContent).toMatch(/does not serve markdown tabs/); // a kind this daemon lacks stays visible, closable

    await fire($(termTab)!, 'dragstart');
    await fire($('.pane[data-group="g1"] .dz.right')!, 'drop'); // the terminal is alone in its pane: it frees one, so this is allowed
    expect($$('.pane')).toHaveLength(4);
    expect($('.toast')).toBeNull();
  });

  it('refuses a fifth pane and says so', async () => {
    const storage = memory();
    let ws = defaultWorkspace({ terminal: `term:${ARCH}` });
    ws = applyPreset(openTab(openTab(ws, 'markdown', { path: 'a.md' }), 'web', { port: 3000 }), 'grid');
    ws = openTab(ws, 'shell', {}, 'g1'); // a second tab in the first pane
    saveWorkspace(storage, storageKey('test', ARCH), ws);
    await mount(ARCH, storage);
    await until(() => $$('.pane').length === 4, 'four panes');
    await fire($('[data-tab="shell:1"]')!, 'dragstart');
    await fire($$('.pane')[3]!.querySelector('.dz.bottom')!, 'drop');
    expect($$('.pane')).toHaveLength(4);
    expect(text($('.toast'))).toMatch(/At most 4 panes/);
    await fire($('[data-tab="shell:1"]')!, 'dragstart');
    await fire($$('.pane')[3]!.querySelector('.strip')!, 'drop');
    expect($$('.pane')).toHaveLength(4); // a merge is never refused
    expect($$('.pane')[3]!.querySelector('[data-tab="shell:1"]')).not.toBeNull();
  });

  it('restores the layout after a reload, and a closed tab stays closed', async () => {
    const storage = memory();
    await mount(ARCH, storage);
    await until(() => $$('.pane').length === 2, 'two panes');
    await fire($(termTab)!, 'dragstart');
    await fire($('.pane[data-group="g1"] .dz.bottom')!, 'drop');
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
  it('starts an agent from its row, then the ⋯ menu stops it and Resume brings it back', async () => {
    const { daemon } = await mount(TB);
    await until(() => $$('.row-wrap').length === 3, 'rows');
    await until(() => $('.row-actions .btn.primary'), 'Start on the selected row');
    expect($$('.row-actions .menu-item').map((b) => text(b.querySelector('strong')))).toEqual(['Move account', 'Settings']); // nothing to stop yet
    await click($('.row-actions .btn.primary')!);
    await until(() => daemon.sessions.get(TB)!.state !== 'parked', 'session started');
    await until(() => $('.row-actions summary'), 'the ⋯ menu');
    expect($$('.row-actions .menu-item').map((b) => text(b.querySelector('strong')))).toEqual([
      'Restart',
      'Restart fresh',
      'Close',
      'Park',
      'Fold into handoff',
      'Move account',
      'Settings',
      'Interrupt',
      'Exit',
      'Kill',
    ]);
    vi.stubGlobal('confirm', () => false);
    await click($$('.row-actions .menu-item').find((b) => text(b).startsWith('Kill'))!);
    expect($('.row-actions summary')).not.toBeNull(); // refused at the confirm
    vi.stubGlobal('confirm', () => true);
    await click($$('.row-actions .menu-item').find((b) => text(b).startsWith('Close'))!);
    await click($$('.verb-sheet .btn').find((b) => text(b) === 'Confirm')!); // Close always asks first
    await until(() => $$('.row-actions .btn').some((b) => text(b).includes('Resume')), 'Resume after close');
  });

  it('keeps the selected row short (no model, account or detail block there)', async () => {
    await mount(ARCH);
    await until(() => $('.row-wrap.on'), 'the selected row');
    expect($('.row-detail')).toBeNull();
    const r = text($('.row-wrap.on'));
    expect(r).not.toMatch(/opus|alpha|buddyron/);
    expect($('.row-wrap.on .name')?.textContent).toBe('architect'); // the team prefix is the group's title
    await until(() => $('.terminal-tab'), 'terminal tab');
    expect($('.row-actions .btn.primary')).toBeNull();
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
    expect(text($('.daemon-down'))).toContain('hl service install');
    m.net.down = false;
    await until(() => !$('.daemon-down'), 'the notice gone');
  });
});
