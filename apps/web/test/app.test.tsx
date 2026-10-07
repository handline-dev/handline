import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, activate, applyPreset, defaultWorkspace, openTab, saveWorkspace, storageKey, type KV } from '@handline/app-core';
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

async function mount(agent: string, storage: KV = textGrid(), s = setup()) {
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

  it('gives the main view the whole window: the sidebar holds what the top bar, status bar and context panel held', async () => {
    await mount(ARCH);
    await until(() => $('.rail'), 'sidebar');
    expect($('.statusbar')).toBeNull();
    expect($('.side')).toBeNull();
    expect(text($('.rail-status'))).toMatch(/daemon open/);
    expect($('.rail input[type=search]')).not.toBeNull();
    expect(text($('.rail-nav'))).toMatch(/Inbox.*Tasks.*Team.*Accounts/);
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
    await click($$('.pane[data-group="g1"] .menu-item').find((b) => text(b) === 'Claude Code')!);
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
    expect($$('.row-actions .menu-item').map((b) => text(b.querySelector('strong')))).toEqual(['Move account']); // nothing to stop yet
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

  it('shows the facts the old header showed under the selected row, and Take control and Release in the terminal tab', async () => {
    await mount(ARCH);
    await until(() => $('.row-detail'), 'detail');
    const d = text($('.row-detail'));
    expect(d).toMatch(/model.*opus/);
    expect(d).toMatch(/account.*alpha/);
    expect(d).toMatch(/task.*Web UI design/);
    expect(d).toMatch(/worktree.*buddyron\/handline\.architect/);
    await until(() => $('.term-bar'), 'terminal tab bar');
    expect(text($('.term-bar'))).toContain('input: daemon');
    await click($$('.term-bar .btn').find((b) => text(b) === 'Take control')!);
    await until(() => text($('.term-bar')).includes('input: YOU'), 'holding the input');
    await click($$('.term-bar .btn').find((b) => text(b) === 'Release')!);
    await until(() => text($('.term-bar')).includes('input: daemon'), 'released');
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
