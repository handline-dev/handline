// The hidden debug window: the pane list and its flags, the metadata block, the terminal of the chosen pane, and the Settings button.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { Terminal } from '@xterm/headless';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { DaemonProvider } from '@handline/app-react';
import { DataProvider, type BoxComponent } from '@handline/app-data';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';
import { DebugEntry, debugUrl } from '../src/debug/DebugEntry.tsx';
import { DebugTerminals } from '../src/debug/DebugTerminals.tsx';
import { cacheLeft, dash, facts, flags, span, type DebugPane } from '../src/debug/paneFacts.ts';
import type { CreateTerm } from '../src/terminal/core.ts';
import { dropKept } from '../src/terminal/keep.ts';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const Box: BoxComponent = (p) => <div {...p} />;
const ARCH = 'handline.architect';

const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}

const pane = (over: Partial<DebugPane> = {}): DebugPane => ({
  session: 'ocp',
  window: '2',
  window_name: 'w',
  terminal: '%7',
  pid: 42,
  command: 'zsh',
  path: '/w',
  dead: false,
  last_activity: '2026-01-01T00:00:00Z',
  idle_s: 3720,
  process: 'shell',
  agent_process: null,
  agent: null,
  orphan: true,
  closed: false,
  recorded_agent: null,
  state: null,
  account: null,
  session_id: null,
  worktree: null,
  cache_until: null,
  orca_terminal: null,
  ...over,
});

describe('pane facts', () => {
  const now = Date.parse('2026-01-01T01:00:00Z');
  it('says unknown as a dash and spans in short words', () => {
    expect([dash(null), dash(''), dash(undefined), dash(0), dash('x')]).toEqual(['-', '-', '-', '0', 'x']);
    expect([span(45), span(720), span(3720), span(90000)]).toEqual(['45s', '12m', '1h 2m', '1d 1h']);
  });
  it('flags an orphan, a shell left after the agent exited, and a session kept past its cache', () => {
    expect(flags(pane(), now).map((f) => f.word)).toEqual(['orphan']);
    expect(flags(pane({ recorded_agent: 'a.b' }), now).map((f) => f.word)).toEqual(['orphan', 'agent exited']);
    const kept = pane({ orphan: false, agent: 'a.b', process: 'agent', cache_until: '2026-01-01T00:50:00Z', agent_process: { pid: 9, name: 'claude' } });
    expect(cacheLeft(kept, now)).toBe(-600);
    expect(flags(kept, now).map((f) => f.word)).toEqual(['cache expired', 'agent running']);
    expect(flags({ ...kept, cache_until: '2026-01-01T02:00:00Z' }, now).map((f) => f.word)).toEqual(['agent running']);
  });
  it('lists every metadata field, "-" for what the daemon does not know', () => {
    const rows = Object.fromEntries(facts(pane({ recorded_agent: 'a.b' }), now));
    expect(rows['Agent']).toBe('none (opened for a.b)');
    expect([rows['Account'], rows['Session id'], rows['Worktree'], rows['Orca terminal'], rows['Cache expiry']]).toEqual(['-', '-', '-', '-', '-']);
    expect(rows['Process']).toBe('shell · pane pid 42 · zsh');
    expect(rows['Last activity']).toBe('2026-01-01T00:00:00Z (1h 2m idle)');
  });
});

describe('the debug window', () => {
  let root: Root | null = null;
  let box: HTMLElement;
  afterEach(async () => {
    await act(async () => root?.unmount());
    dropKept();
    root = null;
    box?.remove();
    location.hash = '';
  });
  const create: CreateTerm = () => {
    const term = new Terminal({ cols: 80, rows: 24, allowProposedApi: true });
    return { term, propose: () => null, setFontSize() {}, mark() {}, focus() {}, dispose: () => term.dispose() };
  };
  async function mount(node: React.ReactNode) {
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(node));
  }
  const $ = (sel: string) => box.querySelector<HTMLElement>(sel);

  it('lists every pane with its flags and shows the chosen one with its metadata and terminal', async () => {
    const s = setup();
    const store = new Store(s.client);
    await mount(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test" box={Box}>
          <DebugTerminals create={create} />
        </DataProvider>
      </DaemonProvider>,
    );
    await until(() => $('[data-pane="%stray"]'), 'the pane list');
    expect($('[data-pane="%stray"] [data-flag="orphan"]')).not.toBeNull();
    expect(box.textContent).toContain('Pick a pane');
    await act(async () => $(`[data-pane="term:${ARCH}"]`)!.click());
    expect($('[data-fact="Agent"] dd')!.textContent).toBe(ARCH);
    expect($('[data-fact="Orca terminal"] dd')!.textContent).toBe('-');
    await until(() => $(`[aria-label="Terminal of ${ARCH}"]`), 'the terminal');
    expect(box.textContent).toContain('Click the terminal to type in it.');
    await act(async () => $('[data-pane="%stray"]')!.click());
    expect($('[data-fact="Agent"] dd')!.textContent).toContain('none (opened for');
  });

  it('is the whole page at its route, with no sidebar', async () => {
    location.hash = '#/debug/terminals';
    const s = setup();
    const memory: KV = { getItem: () => null, setItem() {} };
    await mount(<App store={new Store(s.client)} daemon="test" storage={memory} />);
    await until(() => $('.debug-terminals'), 'the debug page');
    expect($('#app')).toBeNull();
  });

  it('opens from a button at the bottom of Settings in a window of its own', async () => {
    const open = vi.fn();
    await mount(<DebugEntry open={open} />);
    await act(async () => $('button')!.click());
    expect(open).toHaveBeenCalledWith(debugUrl(location));
    expect(debugUrl({ origin: 'http://h', pathname: '/', search: '?a=1' })).toBe('http://h/?a=1#/debug/terminals');
  });
});
