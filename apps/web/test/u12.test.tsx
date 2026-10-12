// U12: the ⋯ menu carries the agent verbs. Each is a decided write with its gerund in the state cell; the bridge's CONFIRM verbs ask in
// a sheet under the row, which re-checks the row on Confirm; a verb the daemon does not serve shows disabled.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { second, setup } from '@handline/fake-daemon/helpers';
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
  it('Park asks first; Cancel sends nothing and Confirm parks, with "parking…" in the state cell meanwhile', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(await item('Sleep'));
    expect(text($('.verb-sheet'))).toContain('Park handline.architect?');
    await click(sheetBtn('Cancel'));
    expect($('.verb-sheet')).toBeNull();
    expect(session(s).open).toBe(true);
    await click(await item('Sleep'));
    const park = s.daemon.knobs.hold('session.park'); // held open: the pending mark does not depend on timing
    await click(sheetBtn('Confirm'));
    await until(() => ($(`[data-agent="${ARCH}"] .row-main`)?.getAttribute('title') ?? '').includes('parking…'), 'the gerund in the row');
    expect(text($(`[data-agent="${ARCH}"] .row-actions [aria-haspopup="menu"]`))).toContain('parking…'); // and in the menu button
    expect(session(s).open).toBe(true);
    park();
    await until(() => !session(s).open, 'parked');
    await until(() => !text($(`[data-agent="${ARCH}"] .row-actions [aria-haspopup="menu"]`)).includes('parking…'), 'the mark gone');
  });

  it('Restart fresh drops the session id; Restart keeps the conversation', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(await item('Restart fresh'));
    expect(text($('.verb-sheet'))).toContain('context is dropped');
    await click(sheetBtn('Confirm'));
    await until(() => session(s).sessionId !== 's-1', 'a fresh session');
    const fresh = session(s).sessionId;
    await click(await item('Restart'));
    await click(sheetBtn('Confirm'));
    await until(() => session(s).open && session(s).sessionId === fresh, 'resumed');
  });

  it('Close always asks, even for an idle agent', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(await item('Close'));
    expect($('.verb-sheet')).not.toBeNull();
    expect(session(s).open).toBe(true);
    await click(sheetBtn('Confirm'));
    await until(() => !session(s).open, 'closed');
  });

  it('Fold asks and ends the session so the next one starts from the handoff', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(await item('Fold into handoff'));
    await click(sheetBtn('Confirm'));
    await until(() => !session(s).open && session(s).sessionId === null, 'folded');
  });

  it('Move account asks for the account, not a yes', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(await item('Move account'));
    expect(sheetBtn('Move').hasAttribute('disabled')).toBe(true);
    const sel = $('.verb-sheet select') as HTMLSelectElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(sel, 'beta');
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await click(sheetBtn('Move'));
    await until(() => session(s).fx.account === 'beta', 'moved');
  });

  it('Move account lists an account no agent uses when the daemon lists accounts', async () => {
    const s = setup();
    s.daemon.knobs.accounts = ['alpha', 'beta', 'gamma'];
    open(s);
    await mount(ARCH, s);
    await click(await item('Move account'));
    await until(() => $$('.verb-sheet option').some((o) => text(o) === 'gamma'), 'gamma among the accounts');
    expect($$('.verb-sheet option').map((o) => text(o))).toEqual(['choose an account', 'alpha', 'gamma']);
  });

  it("a refused verb puts the row back and shows the daemon's reason", async () => {
    const s = setup();
    open(s);
    s.daemon.knobs.refuse('session.park', 'state', 'The agent is mid-turn; park it when it is idle.');
    await mount(ARCH, s);
    await click(await item('Sleep'));
    await click(sheetBtn('Confirm'));
    await until(() => text($('.toast')).includes('mid-turn'), 'the reason');
    expect($(`[data-agent="${ARCH}"] .row-main`)?.getAttribute('title')).not.toContain('parking…');
    expect(session(s).open).toBe(true);
  });

  it('a sheet whose row changed while it was open sends nothing and says so', async () => {
    const s = setup();
    open(s);
    const net = { down: false };
    await mount(ARCH, s);
    await click(await item('Sleep'));
    const other = second(s.daemon, net);
    await other.start();
    await other.call('session.stop', { agent: ARCH, mode: 'close', request_id: 'x1' }); // another client closes it
    await until(() => !session(s).open, 'closed elsewhere');
    await wait(50);
    await click(sheetBtn('Confirm'));
    expect(text($('.verb-sheet'))).toContain('It changed while you looked');
  });

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
