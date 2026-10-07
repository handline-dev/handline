// U15: the HR tab and the Team table. Approve, reject and withdraw are decided writes; the dot counts open and apply_failed proposals and
// follows the hr topic; a proposal that changed or settled under the click sends nothing more and the card says so.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store, type KV } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import type { FakeHr } from '../../../packages/fake-daemon/src/hr.ts';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
const card = (id: number) => $(`[data-proposal="${id}"]`)!;
const btn = (within: Element, label: string) => [...within.querySelectorAll('button')].find((b) => text(b).trim().startsWith(label))!;
const hrOf = (s: ReturnType<typeof setup>) => (s.daemon as unknown as { script: { hr: FakeHr } }).script.hr;
const typeInto = (el: HTMLElement, v: string) =>
  act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

async function open(s: ReturnType<typeof setup>, hash = '#/team/hr') {
  location.hash = hash;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={memory()} />);
  });
}
const raise = (s: ReturnType<typeof setup>, id: number, over: object = {}) =>
  hrOf(s).raise({ id, title: `settings handline.x${id}`, diff: `- effort  high\n+ effort  max`, reason: 'it needs more thought', ...over });

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});

describe('U15 HR', () => {
  it('counts open and apply_failed proposals in the tab and the sidebar, and not settled or unknown ones', async () => {
    const s = setup();
    raise(s, 1);
    raise(s, 2, { state: 'apply_failed', error: 'boom' });
    raise(s, 3, { state: 'approved' });
    raise(s, 4, { state: 'on_hold' });
    await open(s);
    await until(() => $('.hr-card'), 'the cards');
    expect(text($('[role=tab][aria-selected=true]'))).toContain('HR 2');
    expect(text($('.rail-nav'))).toContain('HR 2');
  });

  it('an hr.raised event raises the count with no reload', async () => {
    const s = setup();
    raise(s, 1);
    await open(s);
    await until(() => text($('.rail-nav')).includes('HR 1'), 'one waiting');
    raise(s, 2);
    await until(() => text($('.rail-nav')).includes('HR 2'), 'two waiting');
    expect($$('.hr-card')).toHaveLength(2);
  });

  it('draws each state word, keeps the diff marks as text, and gives an unknown state no buttons', async () => {
    const s = setup();
    raise(s, 1);
    raise(s, 2, { state: 'apply_failed', error: 'x'.repeat(500) });
    raise(s, 3, { state: 'rejected' });
    raise(s, 4, { state: 'withdrawn' });
    raise(s, 5, { state: 'on_hold' });
    await open(s);
    await until(() => $$('.hr-card').length === 5, 'five cards');
    expect(text(card(1))).toContain('waiting for you');
    expect(card(1).querySelector('.hr-state svg[data-icon="needs"]')).not.toBeNull(); // a shape, not only the word's colour
    expect(card(2).querySelector('.hr-state svg[data-icon="blocked"]')).not.toBeNull();
    expect(card(3).querySelector('.hr-state svg[data-icon="dismissed"]')).not.toBeNull(); // rejected has a shape of its own
    expect(text(card(2))).toContain('approved, apply failed');
    expect(text(card(3))).toContain('rejected');
    expect(text(card(4))).toContain('withdrawn');
    expect(text(card(5))).toContain('on_hold');
    expect(text(card(1).querySelector('.hr-diff'))).toContain('- effort  high');
    expect(text(card(1).querySelector('.hr-diff'))).toContain('+ effort  max');
    expect(text(card(2).querySelector('.fnote'))).toHaveLength(401); // cut at 400, with an ellipsis
    expect(btn(card(2), 'Retry')).toBeTruthy();
    expect(card(5).querySelector('button')).toBeNull();
    expect(card(3).querySelector('button')).toBeNull();
  });

  it('Approve sends the subject the card showed and the card moves to Recent when the event arrives', async () => {
    const s = setup();
    const p = raise(s, 1);
    await open(s);
    await until(() => $('.hr-card'), 'the card');
    await click(btn(card(1), 'Approve'));
    await until(() => p.state === 'approved', 'approved');
    await until(() => text($('.hr')).includes('Recent') && text(card(1)).includes('approved and applied'), 'in Recent');
    expect(card(1).querySelector('button')).toBeNull();
  });

  it('Reject with an empty reason sends nothing; with a reason it rejects', async () => {
    const s = setup();
    const p = raise(s, 1);
    await open(s);
    await until(() => $('.hr-card'), 'the card');
    await click(btn(card(1), 'Reject'));
    const input = card(1).querySelector<HTMLElement>('input')!;
    await act(async () => void input.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    expect(p.state).toBe('open');
    await typeInto(input, 'not now');
    await act(async () => void input.closest('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    await until(() => p.state === 'rejected', 'rejected');
  });

  it('a proposal that changed under the click is not decided; the card says so and a second Approve decides the new text', async () => {
    const s = setup();
    const p = raise(s, 1);
    await open(s);
    await until(() => $('.hr-card'), 'the card');
    p.subject = 'new-digest';
    await click(btn(card(1), 'Approve'));
    await until(() => text(card(1)).includes('This proposal changed'), 'the notice');
    expect(p.state).toBe('open');
    await click(btn(card(1), 'Approve'));
    await until(() => p.state === 'approved', 'approved the new text');
  });

  it('a proposal already settled shows "already <state>" with no buttons left to press', async () => {
    const s = setup();
    const p = raise(s, 1);
    await open(s);
    await until(() => $('.hr-card'), 'the card');
    p.state = 'withdrawn'; // settled elsewhere, no event reached this tab
    await click(btn(card(1), 'Approve'));
    await until(() => text(card(1)).includes('withdrawn'), 'the card follows the daemon');
    expect(btn(card(1), 'Approve')).toBeUndefined();
  });

  it('Approve all sends one decide per waiting proposal; one whose apply fails stays waiting', async () => {
    const s = setup();
    const a = raise(s, 1);
    const b = raise(s, 2);
    const c = raise(s, 3);
    hrOf(s).failApply.set(2, 'could not write the file');
    await open(s);
    await until(() => $$('.hr-card').length === 3, 'three cards');
    await click($$('button').find((x) => text(x) === 'Approve all')!);
    expect(text($('.verb-sheet'))).toContain('Approve all 3?');
    await click($$('.verb-sheet button').find((x) => text(x) === 'Approve all')!);
    await until(() => a.state === 'approved' && c.state === 'approved' && b.state === 'apply_failed', 'all decided');
    await until(() => text(card(2)).includes('could not write the file'), 'the failure shows');
    expect(btn(card(2), 'Retry')).toBeTruthy();
  });

  it('Withdraw asks first, then takes the proposal back', async () => {
    const s = setup();
    const p = raise(s, 1);
    await open(s);
    await until(() => $('.hr-card'), 'the card');
    await click(btn(card(1), 'Withdraw'));
    expect(text($('.verb-sheet.danger'))).toContain('Takes the proposal back');
    expect(p.state).toBe('open');
    await click($$('.verb-sheet button').find((x) => text(x) === 'Withdraw')!);
    await until(() => p.state === 'withdrawn', 'withdrawn');
  });

  it('a member reads the cards but has no decide control', async () => {
    const s = setup({ role: 'member' });
    raise(s, 1);
    await open(s);
    await until(() => $('.hr-card'), 'the card');
    expect(card(1).querySelector('button')).toBeNull();
    expect($$('button').some((b) => text(b) === 'Approve all')).toBe(false);
  });

  it('a deep link opens the HR tab on its card', async () => {
    const s = setup();
    raise(s, 7);
    await open(s, '#/team/hr/7');
    await until(() => card(7), 'the card');
    expect(card(7).className).toContain('focus');
  });
});

describe('U15 Team', () => {
  it('lists the agents with their roles and, expanded, who each may ask and be asked by', async () => {
    const s = setup();
    await open(s, '#/team');
    await until(() => $('.team-card'), 'the rows');
    expect($$('.team-card').length).toBeGreaterThan(1);
    const name = $('.team-card')!.dataset.agent!;
    await click($(`.team-card[data-agent="${name}"]`)!);
    expect(text($('.edges'))).toContain('May ask');
    expect(text($('.edges'))).toContain('May be asked by');
  });

  it('draws a parked agent’s card with the parked glyph, not the idle ring', async () => {
    const s = setup();
    await open(s, '#/team');
    await until(() => $('.team-card[data-agent="handline.term-build"] .ui-state'), 'the card state');
    expect($('.team-card[data-agent="handline.term-build"] .ui-state')!.dataset.state).toBe('parked');
  });

  it('groups the cards by who they report to', async () => {
    const s = setup();
    await open(s, '#/team');
    await until(() => $('.team-card'), 'the rows');
    const group = $('[aria-label="Reports to handline.architect"]')!;
    expect([...group.querySelectorAll('.team-card')].map((c) => (c as HTMLElement).dataset.agent)).toEqual([
      'handline.auditor',
      'handline.term-build',
    ]);
    expect($('[aria-label="Top of the team"] .team-card')).not.toBeNull();
  });
});
