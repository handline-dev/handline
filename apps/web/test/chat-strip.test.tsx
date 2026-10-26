// The strip under the composer: model and account menus that apply at once, the context and cache in words; the head's settings gear and ⌘F.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCH = 'handline.architect';
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
const $ = (sel: string) => box.querySelector<HTMLElement>(sel);
const items = () => [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')];
const text = (el: Element | null) => el?.textContent ?? '';

async function mount(state?: string, s = setup()) {
  if (state) s.daemon.sessions.get(ARCH)!.state = state as never;
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => $('.chat-strip'), 'the strip');
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});
const chip = (name: string) => $(`.chat-strip [title^="${name}"]`)!;

describe('the chat strip', () => {
  it('says the context as used / limit and the cache as time left', async () => {
    await mount();
    await until(() => text($('.chat-strip')).includes('74k / 200k'), 'the context words');
    expect(text($('.chat-strip'))).toMatch(/\d+m|lapsed/);
  });

  it('the context and cache gauges open their details on click', async () => {
    await mount();
    await until(() => $('.chat-strip [aria-label="Context window details"]'), 'the context gauge');
    await act(async () => $('.chat-strip [aria-label="Context window details"]')!.click());
    await until(() => text(document.body).includes('74,000'), 'the context details');
    await act(async () => document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    await act(async () => $('.chat-strip [aria-label="Prompt cache details"]')!.click());
    await until(() => text(document.body).includes('time until the cache lapses'), 'the cache details');
  });

  it('the model menu lists the efforts, checks the current one and a pick is applied at once', async () => {
    const s = await mount();
    await act(async () => chip('Model').click());
    await until(() => items().length, 'the model menu');
    expect(items().filter((i) => ['low', 'medium', 'high', 'xhigh', 'max'].includes(text(i).trim()))).toHaveLength(5);
    await act(async () =>
      items()
        .find((i) => text(i).trim() === 'low')!
        .click(),
    );
    await until(() => s.daemon.sessions.get(ARCH)!.settings.effort === 'low', 'the effort applied');
  });

  it('the model chip opens a menu and a pick is applied at once', async () => {
    const s = await mount();
    await act(async () => chip('Model').click());
    await until(() => items().length, 'the model menu');
    await act(async () =>
      items()
        .find((i) => text(i).trim() === 'sonnet')!
        .click(),
    );
    await until(() => text(chip('Model')).includes('sonnet'), 'the new model');
    expect(s.daemon.sessions.get(ARCH)!.settings.model).toBe('sonnet');
  });

  it('a model picked while the agent works says it applies when the work ends', async () => {
    await mount('running');
    await act(async () => chip('Model').click());
    await until(() => items().length, 'the model menu');
    await act(async () =>
      items()
        .find((i) => text(i).trim() === 'haiku')!
        .click(),
    );
    await until(() => text(chip('Model')).includes('applies when current work ends'), 'the note');
  });

  it('the account chip moves the agent to the account picked', async () => {
    const s = await mount();
    await act(async () => chip('Account').click());
    await until(() => items().length > 1, 'the account menu');
    const label = (i: Element) => text(i.querySelector('.acct-name')).trim();
    const other = items().find((i) => !text(chip('Account')).includes(label(i)))!;
    const name = label(other);
    await act(async () => other.click());
    await until(() => s.daemon.sessions.get(ARCH)!.fx.account === name, 'the move');
    await until(() => text(chip('Account')).includes(name), 'the chip');
  });

  it('each account in the menu shows the room left in its 5 h window, or says there is no reading', async () => {
    const s = setup();
    s.daemon.accounts.add({ name: 'quiet', five_hour: { used_pct: 12, resets_at: null, source: 'measured' } });
    s.daemon.accounts.add({ name: 'unread' });
    await mount(undefined, s);
    await act(async () => chip('Account').click());
    await until(() => items().length > 1, 'the account menu');
    const row = (n: string) => items().find((i) => text(i.querySelector('.acct-name')).trim() === n)!;
    expect(row('quiet').querySelector('.room')!.getAttribute('data-left')).toBe('88');
    expect(text(row('quiet'))).toContain('88% left');
    expect(text(row('unread'))).toContain('no reading');
  });

  it('the tab strip has the settings gear; the find bar waits for ⌘F and Escape closes it', async () => {
    await mount();
    expect($('.chat-search')).toBeNull();
    await act(async () => void dispatchEvent(new KeyboardEvent('keydown', { key: 'f', metaKey: true })));
    await until(() => $('.chat-search input'), 'the find bar');
    await act(async () => void $('.chat-search input')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect($('.chat-search')).toBeNull();
    await act(async () => $(`.strip [aria-label="${ARCH} settings"]`)!.click());
    expect(location.hash).toBe(`#/agents/${ARCH}/settings`);
  });
});

describe('the terminal view of a chat', () => {
  const flip = (label: string) =>
    [...box.querySelectorAll<HTMLElement>('.chat-foot button')].find((b) => b.getAttribute('aria-label')?.startsWith(label))!;
  it('shows the same footer strip under the terminal, with the way back to the chat', async () => {
    await mount();
    await act(async () => flip('Claude Code').click());
    await until(() => $('.terminal-tab'), 'the terminal view');
    expect($('.chat-foot .chat-strip [title^="Model"]')).not.toBeNull();
    expect(flip('Back to the chat')).toBeDefined();
  });

  it('with no terminal running offers one Start terminal button, which starts it', async () => {
    const s = setup();
    s.daemon.sessions.get(ARCH)!.open = false; // closed, so no terminal
    s.daemon.sessions.get(ARCH)!.hasTerminal = false;
    await mount(undefined, s);
    await act(async () => flip('Claude Code')?.click());
    await until(() => $('.start-terminal'), 'the start button');
    expect(text($('.terminal-tab'))).not.toMatch(/no terminal/i);
    await act(async () => $('.start-terminal button')!.click());
    await until(() => s.daemon.sessions.get(ARCH)!.hasTerminal, 'the terminal started');
  });
});
