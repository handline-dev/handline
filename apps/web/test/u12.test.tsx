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
  const data = new Map<string, string>([['terminal', 'text']]);
  return { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
};

let root: Root | null = null;
let box: HTMLElement;
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const $$ = (sel: string) => [...box.querySelectorAll<HTMLElement>(sel)];
const click = (el: Element) => act(async () => void (el as HTMLElement).click());
const text = (el: Element | null) => el?.textContent ?? '';
const item = (label: string) => $$('.row-actions .menu-item').find((b) => text(b.querySelector('strong')) === label)!;
const sheetBtn = (label: string) => $$('.verb-sheet .btn').find((b) => text(b) === label)!;

async function mount(agent: string, s = setup()) {
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
  await until(() => $('.row-actions summary') && $('.row-actions .menu-item'), 'the ⋯ menu');
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
    s.daemon.knobs.latency(1000);
    await mount(ARCH, s);
    await click(item('Park'));
    expect(text($('.verb-sheet'))).toContain('Park handline.architect?');
    await click(sheetBtn('Cancel'));
    expect($('.verb-sheet')).toBeNull();
    expect(session(s).open).toBe(true);
    await click(item('Park'));
    await click(sheetBtn('Confirm'));
    await until(() => text($(`[data-agent="${ARCH}"] .sub`)).includes('parking…'), 'the gerund');
    await until(() => !session(s).open, 'parked', 1000);
  }, 20_000);

  it('Restart fresh drops the session id; Restart keeps the conversation', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(item('Restart fresh'));
    expect(text($('.verb-sheet'))).toContain('context is dropped');
    await click(sheetBtn('Confirm'));
    await until(() => session(s).sessionId !== 's-1', 'a fresh session');
    const fresh = session(s).sessionId;
    await until(() => item('Restart'), 'the menu back');
    await click(item('Restart'));
    await click(sheetBtn('Confirm'));
    await until(() => session(s).open && session(s).sessionId === fresh, 'resumed');
  });

  it('Close always asks, even for an idle agent', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(item('Close'));
    expect($('.verb-sheet')).not.toBeNull();
    expect(session(s).open).toBe(true);
    await click(sheetBtn('Confirm'));
    await until(() => !session(s).open, 'closed');
  });

  it('Fold asks and ends the session so the next one starts from the handoff', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(item('Fold into handoff'));
    await click(sheetBtn('Confirm'));
    await until(() => !session(s).open && session(s).sessionId === null, 'folded');
  });

  it('Move account asks for the account, not a yes', async () => {
    const s = setup();
    open(s);
    await mount(ARCH, s);
    await click(item('Move account'));
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
    await click(item('Move account'));
    await until(() => $$('.verb-sheet option').some((o) => text(o) === 'gamma'), 'gamma among the accounts');
    expect($$('.verb-sheet option').map((o) => text(o))).toEqual(['choose an account', 'alpha', 'gamma']);
  });

  it("a refused verb puts the row back and shows the daemon's reason", async () => {
    const s = setup();
    open(s);
    s.daemon.knobs.refuse('session.park', 'state', 'The agent is mid-turn; park it when it is idle.');
    await mount(ARCH, s);
    await click(item('Park'));
    await click(sheetBtn('Confirm'));
    await until(() => text($('.toast')).includes('mid-turn'), 'the reason');
    expect(text($(`[data-agent="${ARCH}"] .sub`))).not.toContain('parking…');
    expect(session(s).open).toBe(true);
  });

  it('Wake on a parked agent asks nothing', async () => {
    const s = setup();
    session(s, TB).sessionId = 's-9';
    await mount(TB, s);
    await click(item('Wake'));
    expect($('.verb-sheet')).toBeNull();
    await until(() => session(s, TB).open, 'woken');
  });

  it('a sheet whose row changed while it was open sends nothing and says so', async () => {
    const s = setup();
    open(s);
    const net = { down: false };
    await mount(ARCH, s);
    await click(item('Park'));
    const other = second(s.daemon, net);
    await other.start();
    await other.call('session.stop', { agent: ARCH, mode: 'close', request_id: 'x1' }); // another client closes it
    await until(() => !session(s).open, 'closed elsewhere');
    await wait(50);
    await click(sheetBtn('Confirm'));
    expect(text($('.verb-sheet'))).toContain('It changed while you looked');
  });

  it('a stalled row offers Clear stall, disabled because the daemon does not serve agent.unstall', async () => {
    const s = setup();
    open(s);
    session(s).state = 'stalled';
    await mount(ARCH, s);
    const clear = item('Clear stall');
    expect(clear.hasAttribute('disabled')).toBe(true);
    expect(clear.getAttribute('title')).toContain('agent.unstall');
  });
});
