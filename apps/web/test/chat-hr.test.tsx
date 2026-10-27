// HR proposals in the chat: an open one is a card with approve and reject, a decided one is a compact line at the time it was settled.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string, tries = 400) {
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
const text = (el: Element | null | undefined) => el?.textContent ?? '';
const msg = (body: string) => $$('.msg').find((m) => text(m).includes(body));
const hrOf = (t: { daemon: unknown }) => (t.daemon as { script: { hr: { raise: (p: object) => unknown } } }).script.hr;
const labelled = (within: Element | null | undefined, label: string) =>
  [...(within ?? box).querySelectorAll('button')].find((b) => b.getAttribute('aria-label') === label);

async function mount(agent: string, s = setup()) {
  location.hash = `#/agents/${agent}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('HR proposals in the chat', () => {
  it('open HR proposals about the agent are one line in the chat that opens My turn, with no buttons to decide there', async () => {
    const s = setup();
    hrOf(s).raise({ id: 41, title: 'link handline.architect -> handline.auditor', diff: 'link', agent: ARCH });
    hrOf(s).raise({ id: 42, title: 'settings handline.architect', diff: 'effort', agent: ARCH });
    await mount(ARCH, s);
    await until(() => $('.card-hr'), 'the HR line');
    expect($$('.card-hr')).toHaveLength(1);
    expect(text($('.card-hr'))).toContain('HR proposals: 2 · needs you');
    expect(labelled($('.card-hr'), 'Approve')).toBeUndefined();
    expect(labelled($('.card-hr'), 'Reject')).toBeUndefined();
    await click(labelled($('.card-hr'), 'Open in HR')!);
    expect(location.hash).toBe('#/team/hr');
  });

  it('a proposal already decided keeps its place in the timeline, by the time it was settled, and is not pinned after the newest message', async () => {
    const s = setup();
    const hr = (
      s.daemon as unknown as { script: { hr: { raise: (p: object) => { id: number }; settle: (p: object, st: string) => void } } }
    ).script.hr;
    const p = hr.raise({ id: 43, title: 'context handline.pr', diff: 'replace ROLE.md', agent: ARCH });
    hr.settle(p, 'approved');
    (p as unknown as { settled: string }).settled = '2000-01-01T00:00:00Z'; // before every message
    await mount(ARCH, s);
    await until(() => msg('Which gap list'), 'history');
    const line = $('.card-hr.decided')!;
    expect(text(line)).toContain('context handline.pr');
    const all = [...document.querySelectorAll('.stream *')];
    expect(all.indexOf(line)).toBeLessThan(all.indexOf(msg('Which gap list')!));
  });
  it('a held proposal (approved, applying when the turn ends) is not the founder turn: no line in the chat', async () => {
    const s = setup();
    hrOf(s).raise({
      id: 44,
      title: 'settings handline.architect',
      diff: 'effort: medium -> high',
      agent: ARCH,
      state: 'applying',
      waiting_on: [ARCH],
    });
    hrOf(s).raise({ id: 47, title: 'link a -> b', diff: 'link', agent: ARCH });
    await mount(ARCH, s);
    await until(() => $('.card-hr'), 'the line');
    expect($$('.card-hr')).toHaveLength(1);
    expect(text($('.card-hr'))).toContain('HR proposals: 1 · needs you');
  });

  it('in the HR tab a held card sits with the waiting ones, has no decide buttons and is not in the count', async () => {
    const s = setup();
    hrOf(s).raise({ id: 45, title: 'link a -> b', diff: 'link', agent: ARCH });
    hrOf(s).raise({ id: 46, title: 'settings handline.architect', diff: 'effort', agent: ARCH, state: 'applying', waiting_on: [ARCH] });
    location.hash = '#/team/hr';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />));
    await until(() => $('[data-proposal="46"]'), 'the held card');
    const held = $('[data-proposal="46"]')!;
    expect(text(held)).toContain(`Held until ${ARCH} finishes its turn.`);
    const buttons = (el: Element) => [...el.querySelectorAll('button')].map((b) => text(b).trim());
    const has = (el: Element, word: string) => buttons(el).some((b) => b.startsWith(word));
    expect(has(held, 'Approve') || has(held, 'Reject')).toBe(false);
    expect(has($('[data-proposal="45"]')!, 'Approve')).toBe(true);
    // two cards of ours are shown, one is waiting: the held one is not counted
    expect(text($('.hr-head strong'))).toBe('1 waiting');
    const order = $$('.hr-card').map((c) => c.dataset.proposal);
    expect(order.filter((id) => id === '46')).toHaveLength(1); // not again under Recent
    expect(order.indexOf('46')).toBeGreaterThan(order.indexOf('45'));
  });

  it('a held card says since when it waits and Apply now applies it at once', async () => {
    const s = setup();
    hrOf(s).raise({
      id: 47,
      title: 'rename handline.chat',
      diff: 'rename',
      agent: ARCH,
      state: 'applying',
      waiting_on: [ARCH],
      waiting_since: new Date(Date.now() - 600_000).toISOString(),
    });
    location.hash = '#/team/hr';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />));
    await until(() => $('[data-proposal="47"]'), 'the held card');
    expect(text($('[data-proposal="47"]')!)).toMatch(/finishes its turn, since 10m/);
    await click([...$('[data-proposal="47"]')!.querySelectorAll('button')].find((b) => text(b).trim() === 'Apply now')!);
    await until(() => !$('[data-proposal="47"] .hr-held'), 'the card applied');
  });
});
