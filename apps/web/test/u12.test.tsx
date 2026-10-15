// U12: the ⋯ menu carries the agent verbs. Each is a decided write with its gerund in the state cell; the bridge's CONFIRM verbs ask in
// a sheet under the row, which re-checks the row on Confirm; a verb the daemon does not serve shows disabled.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 300) {
  for (let i = 0; i < tries; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const memory = (): KV => {
  const data = new Map<string, string>([['terminal', 'text']]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null) => el?.textContent ?? '';
const item = async (label: string) => {
  if (!document.querySelector('[role=menu]')) await click($('.row-actions [aria-haspopup="menu"]')!);
  await until(() => document.querySelector('[role=menu]'), 'the ⋯ menu');
  return [...document.querySelectorAll('[role=menuitem]')].find((b) => text(b) === label)!;
};
const sheetBtn = (label: string) => $$('.verb-sheet .ui-btn').find((b) => text(b) === label)!;

async function mount(agent: string, s = setup()) {
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('.row-actions [aria-haspopup="menu"]'), 'the ⋯ menu');
  return s;
}
const session = (s: ReturnType<typeof setup>, a = ARCH) => s.daemon.sessions.get(a)!;
const open = (s: ReturnType<typeof setup>, a = ARCH) => {
  session(s, a).open = true;
  session(s, a).state = 'idle';
  session(s, a).sessionId = 's-1';
};

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('U12 agent verbs', () => {
  it('a stalled row offers Clear stall, which asks first and then queues the agent', async () => {
    const s = setup();
    open(s);
    session(s).state = 'stalled';
    await mount(ARCH, s);
    await click(await item('Clear stall'));
    expect(text($('.verb-sheet'))).toContain('Clear stall handline.architect?');
    await click(sheetBtn('Cancel'));
    expect(session(s).state).toBe('stalled');
    await click(await item('Clear stall'));
    const clear = s.daemon.knobs.hold('agent.unstall');
    await click(sheetBtn('Confirm'));
    await until(() => text($(`[data-agent="${ARCH}"] .row-actions [aria-haspopup="menu"]`)).includes('waking…'), 'the gerund in the menu button');
    expect(session(s).state).toBe('stalled');
    clear();
    await until(() => session(s).state === 'queued', 'cleared');
    await until(() => !text($(`[data-agent="${ARCH}"] .row-actions [aria-haspopup="menu"]`)).includes('waking…'), 'the mark gone');
  });

  it('leaves Clear stall out of the menu on a daemon that does not serve agent.unstall', async () => {
    const s = setup();
    open(s);
    session(s).state = 'stalled';
    s.daemon.knobs.unserve('agent.unstall');
    await mount(ARCH, s);
    await item('Copy path'); // opens the menu
    expect([...document.querySelectorAll('[role=menuitem]')].map((b) => text(b))).not.toContain('Clear stall');
  });
});
