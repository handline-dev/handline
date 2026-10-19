// HR proposals in the chat: an open one is a card with approve and reject, a decided one is a compact line at the time it was settled.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

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
const typeInto = (el: HTMLInputElement, value: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
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
  it('an open HR proposal about the agent is a card in the chat with approve and reject as icons', async () => {
    const s = setup();
    hrOf(s).raise({ id: 41, title: 'link handline.architect -> handline.auditor', diff: 'link', agent: ARCH });
    await mount(ARCH, s);
    await until(() => $('.card-hr'), 'the HR card');
    expect(text($('.card-hr'))).toContain('link handline.architect');
    expect(labelled($('.card-hr'), 'Approve')).toBeDefined();
    expect(labelled($('.card-hr'), 'Reject')).toBeDefined();
    await click(labelled($('.card-hr'), 'Reject')!);
    await typeInto($('.ui-dialog input') as HTMLInputElement, 'not now');
    await click($('.ui-dialog .ui-btn.solid')!);
    await until(() => $('.card-hr')?.getAttribute('data-state') === 'rejected', 'the card decided');
    expect(text($('.card-hr'))).toContain('Rejected by you');
    expect(labelled($('.card-hr'), 'Approve')).toBeUndefined(); // a decided one is a line, not a card with buttons
  });

  it('an approved HR proposal becomes one compact line saying Applied by you, and the app posts nothing in the chat', async () => {
    const s = setup();
    hrOf(s).raise({ id: 42, title: 'link handline.architect -> handline.auditor', diff: 'link architect to auditor', agent: ARCH });
    await mount(ARCH, s);
    await until(() => $('.card-hr'), 'the HR card');
    await click(labelled($('.card-hr'), 'Approve')!);
    await until(() => $('.card-hr')?.getAttribute('data-state') === 'approved', 'the line');
    expect(text($('.card-hr'))).toContain('Applied by you');
    expect($$('.card-hr')).toHaveLength(1);
    expect($$('.msg').some((m) => /applied/i.test(text(m)))).toBe(false);
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
});

describe('what a decided proposal did', () => {
  it('names the team and model of a new agent and the settings a set changed, on the same line', async () => {
    const s = setup();
    const hr = (s.daemon as unknown as { script: { hr: { raise: (p: object) => object; settle: (p: object, st: string) => void } } }).script
      .hr;
    hr.settle(
      hr.raise({
        id: 51,
        title: 'add handline.sea',
        kind: 'add',
        diff: 'register',
        agent: ARCH,
        asked: { team: 'handline', model: 'opus' },
      }),
      'approved',
    );
    hr.settle(
      hr.raise({
        id: 52,
        title: 'settings handline.sea',
        kind: 'settings',
        diff: 'set',
        agent: ARCH,
        asked: { sets: { model: 'sonnet' } },
      }),
      'approved',
    );
    await mount(ARCH, s);
    await until(() => $$('.card-hr.decided').length === 2, 'both lines');
    const line = (id: number) => text($(`.card-hr[data-proposal="${id}"]`));
    expect(line(51)).toMatch(/Applied by you.*team handline, model opus/);
    expect(line(52)).toMatch(/Applied by you.*model sonnet/);
    expect(line(51)).not.toContain('.bak');
  });
});
