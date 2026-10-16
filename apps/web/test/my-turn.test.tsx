// The My turn page: cards from the inbox, agents that need the person and reports to accept, most urgent first.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
const ME = 'web-test'; // the name the fake setup gives its client
const wait = (ms = 10) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
async function until(cond: () => unknown, what: string) {
  for (let i = 0; i < 300; i++) {
    if (cond()) return;
    await wait();
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
let box: HTMLElement;
let keep = () => {};
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});
const cards = () => [...box.querySelectorAll<HTMLElement>('.my-turn-card')];

describe('my turn', () => {
  it('shows a dialog agent as one card with only Open terminal, and counts it in the rail', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined);
    fx.view = {
      needs_you: { kind: 'dialog', since: new Date(Date.now() - 5 * 60_000).toISOString(), text: 'Trust this folder?', source: 'hook' },
    };
    location.hash = '#/my-turn';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    await until(() => cards().some((c) => c.dataset.kind === 'dialog'), 'the dialog card');
    const dialog = cards().find((c) => c.dataset.kind === 'dialog')!;
    expect(dialog.textContent).toContain('Trust this folder?');
    expect([...dialog.querySelectorAll('button')].map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual(['Open terminal']);
    expect(box.querySelector('.rail-nav')!.textContent).toMatch(/My turn \d+/);
  });
  async function open(s = setup()) {
    s.daemon.tasks.verbs = true;
    location.hash = '#/my-turn';
    box = document.body.appendChild(document.createElement('div'));
    root = createRoot(box);
    await act(async () => {
      root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
    });
    return s;
  }
  const label = (b: Element) => b.getAttribute('title') ?? b.getAttribute('aria-label') ?? b.textContent ?? '';
  const button = (c: Element, name: string) => [...c.querySelectorAll('button')].find((b) => label(b) === name);
  const click = (el: Element | undefined) => act(async () => void (el as HTMLElement).click());
  const type = (el: Element, v: string) =>
    act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, v);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    });
  const report = (s: ReturnType<typeof setup>, id: number, from: string) =>
    s.daemon.tasks.seed({
      id,
      from,
      to: ARCH,
      title: `Report ${id}`,
      state: 'reported',
      report: { by: ARCH, evidence: 'built', at: new Date().toISOString() },
    });

  it('Accept closes a report that answers to the founder, and no card is made for one that answers to an agent', async () => {
    const s = setup();
    report(s, 9001, ME);
    report(s, 9002, 'handline.auditor');
    await open(s);
    await until(() => box.querySelector('[data-card="accept:9001"]'), 'the accept card');
    expect(box.querySelector('[data-card="accept:9002"]')).toBeNull();
    await click(button(box.querySelector('[data-card="accept:9001"]')!, 'Accept'));
    await until(() => s.daemon.tasks.get({ task: 9001 }).state === 'done', 'the daemon');
  });

  it('Send back asks what is missing and sends it', async () => {
    const s = setup();
    report(s, 9001, ME);
    await open(s);
    await until(() => box.querySelector('[data-card="accept:9001"]'), 'the accept card');
    const c = box.querySelector('[data-card="accept:9001"]')!;
    await click(button(c, 'More'));
    await click([...document.body.querySelectorAll('[role=menuitem]')].find((b) => b.textContent === 'Send back')); // the menu is a portal
    const field = c.querySelector('input, textarea')!;
    await act(async () => {
      const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement : HTMLInputElement;
      Object.getOwnPropertyDescriptor(proto.prototype, 'value')!.set!.call(field, 'Tests missing');
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await click(button(c, 'Send back'));
    await until(() => s.daemon.tasks.get({ task: 9001 }).state === 'queued', 'the daemon');
  });

  it('a row opens its task in a detail beside the list on a wide window; Back closes it', async () => {
    const media = (matches: boolean) => vi.stubGlobal('matchMedia', () => ({ matches, addEventListener() {}, removeEventListener() {} }));
    media(true);
    keep = () => void vi.unstubAllGlobals();
    const s = setup();
    report(s, 9002, ME);
    await open(s);
    await until(() => box.querySelector('[data-card="accept:9002"]'), 'the accept card');
    const c = box.querySelector('[data-card="accept:9002"]')!;
    await click(c.querySelector('[role=button]') ?? undefined);
    await until(() => box.querySelector('.tdetail'), 'the task detail');
    expect(box.querySelector('.my-turn-split .my-turn')).not.toBeNull(); // beside the list, not instead of it
    expect(box.querySelector('[data-card="accept:9002"]')!.getAttribute('aria-current')).toBe('true');
    await until(() => box.querySelector('.tdetail .tbody'), 'the task body');
    // jsdom does not match :focus-visible, so the cascade is checked in the rules: a later, more specific one than the base ring.
    const css = readFileSync(join(process.cwd(), 'src/styles/base.css'), 'utf8');
    expect(css.indexOf('.tbody:focus-visible')).toBeGreaterThan(css.indexOf('\n:focus-visible'));
    expect(/\.tbody:focus-visible\s*\{\s*outline:\s*none;/.test(css)).toBe(true);
    expect(box.querySelector('.tdetail .tbody')).not.toBeNull();
    await click(box.querySelector('.tdetail .back') ?? undefined);
    expect(box.querySelector('.tdetail')).toBeNull();
  });

  it("shows the daemon's refusal on the card and keeps Accept usable", async () => {
    const s = setup();
    report(s, 9001, ME);
    await open(s);
    await until(() => box.querySelector('[data-card="accept:9001"]'), 'the accept card');
    s.daemon.tasks.verbs = false; // the daemon does not serve task.accept
    const c = box.querySelector('[data-card="accept:9001"]')!;
    await click(button(c, 'Accept'));
    await until(() => c.querySelector('[role=alert]'), 'the refusal');
    expect(button(c, 'Accept')!.hasAttribute('disabled')).toBe(false);
  });

  it('Reply sends the written answer of a decision', async () => {
    const s = await open();
    await until(() => box.querySelector('[data-card="item:3"]'), 'the decision card');
    const c = box.querySelector('[data-card="item:3"]')!;
    expect(c.querySelector('[role=button]')).toBeNull(); // no task to open, and no clamped text to unfold
    expect(c.querySelector('.line-clamp-3')).toBeNull();
    await click(button(c, 'Reply'));
    await type(c.querySelector('input')!, 'Yes, relay it.');
    await click(button(c, 'Send reply'));
    await until(() => s.daemon.inbox.get(3).read || s.daemon.inbox.get(3).answered, 'the daemon');
  });

  it('a permission prompt without yes/no options is a card with only Open terminal', async () => {
    await open();
    await until(() => box.querySelector('[data-card="item:4"]'), 'the approval card');
    const c = box.querySelector('[data-card="item:4"]')!;
    expect(c.getAttribute('data-kind')).toBe('approval');
    expect(button(c, 'Allow')).toBeUndefined();
  });
});
