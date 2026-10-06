// Q2: the web app reads and writes through app-data. Loading shows skeletons, a decided write shows a pending mark, a refusal shows
// the daemon's sentence and puts the control back, a stale prompt refreshes the card.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const TB = 'handline.term-build';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>();
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null) => el?.textContent ?? '';
const button = (label: string) => $$('button').find((b) => text(b).includes(label));

async function mount(agent: string, s = setup()) {
  const store = new Store(s.client);
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={store} daemon="test" storage={memory()} />);
  });
  return { store, ...s };
}

beforeEach(() => void vi.stubGlobal('confirm', () => true));
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  vi.unstubAllGlobals();
});

describe('loading', () => {
  it('the sidebar shows its skeleton after the delay, no spinner, then the rows once the daemon answers', async () => {
    const s = setup();
    s.daemon.knobs.latency(500);
    await mount(TB, s);
    expect($('.rail-list .skeleton')).toBeNull(); // not before 150 ms: a quick answer shows no flash
    await until(() => $('.rail-list .skeleton'), 'the sidebar skeleton');
    const sk = $('.rail-list .skeleton')!;
    expect(sk.getAttribute('aria-busy')).toBe('true');
    expect(sk.getAttribute('aria-label')).toBe('Loading the agent list');
    expect($('.spinner, [role=progressbar]')).toBeNull();
    expect($$('.row-wrap')).toHaveLength(0);
    await until(() => $$('.row-wrap').length === 3, 'the rows', 600);
    await until(() => !$('.rail-list .skeleton'), 'the skeleton gone');
  });

  it('the workspace shows the terminal skeleton while the agent detail loads, then its tabs', async () => {
    const s = setup();
    await mount(ARCH, s);
    await until(() => $('[data-tab="chat"]'), 'workspace');
    s.daemon.knobs.latency(500);
    await act(async () => {
      location.hash = `#/agents/${TB}`;
      dispatchEvent(new Event('hashchange'));
    });
    await until(() => $('.workspace-loading .skeleton'), 'the workspace skeleton');
    await until(() => $('[data-tab="chat"]'), 'the workspace after load', 600);
    expect($('.workspace-loading')).toBeNull();
  });

  it('a daemon that does not list the agent list says so instead of loading forever', async () => {
    const s = setup();
    s.daemon.knobs.refuse('agents.list', 'unsupported', 'not served');
    await mount(ARCH, s);
    await until(() => text($('.rail-list')).includes('does not serve'), 'the not-served sentence');
  });
});

describe('writes', () => {
  it('Start is a pending mark while the daemon works, and nothing else changes until the daemon says so', async () => {
    const s = setup();
    await mount(TB, s);
    await until(() => button('Start'), 'Start');
    s.daemon.knobs.latency(300);
    await click(button('Start')!);
    expect(text(button('starting')!)).toBe('starting…');
    expect(button('starting')!.hasAttribute('disabled')).toBe(true);
    expect(s.daemon.sessions.get(TB)!.state).toBe('parked');
    await until(() => !button('starting'), 'the mark gone', 600);
    expect(s.daemon.sessions.get(TB)!.state).not.toBe('parked');
  });

  it("a refused Start shows the daemon's sentence and hint, and Start is back", async () => {
    const s = setup();
    await mount(TB, s);
    await until(() => button('Start'), 'Start');
    s.daemon.knobs.refuse('session.start', 'cap', 'The account is at its cap', 'wait for the window');
    await click(button('Start')!);
    await until(() => $('.toast'), 'the toast');
    expect(text($('.toast'))).toContain('The account is at its cap (wait for the window)');
    expect(button('starting')).toBeUndefined();
    expect(button('Start')!.hasAttribute('disabled')).toBe(false);
    expect(s.daemon.sessions.get(TB)!.state).toBe('parked');
  });

  it('the prompt card answers as a decided write and a stale prompt refreshes the card without sending anything', async () => {
    const s = setup();
    const prompt = (id: string) => ({
      id,
      agent: ARCH,
      kind: 'permission',
      tool: 'Bash',
      command: `git push ${id}`,
      reason: null,
      lines: [],
      since: '2026-10-07T00:00:00Z',
      options: [{ key: '1', label: 'Yes', answer: 'yes' as const, highlighted: true }],
    });
    s.daemon.sessions.get(ARCH)!.prompt = prompt('old');
    await mount(ARCH, s);
    await until(() => $('.card.prompt'), 'the prompt card');
    expect(text($('.card.prompt'))).toContain('git push old');
    s.daemon.sessions.get(ARCH)!.prompt = prompt('new'); // changed while the person was reading
    await click(button('Yes')!);
    await until(() => text($('.card.prompt')).includes('git push new'), 'the card refreshed');
    expect(s.daemon.sessions.get(ARCH)!.prompt!.id).toBe('new'); // nothing was answered
    expect(text($('.toast'))).toContain('no longer the open one');
  });
});
