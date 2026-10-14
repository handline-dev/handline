// The needs-you card in an agent's chat and the kind chip on its rail row.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setup } from '@handline/fake-daemon/helpers';
import { App } from '../src/App.tsx';

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
const card = () => box.querySelector<HTMLElement>('.card-needs');
const chip = () => box.querySelector<HTMLElement>(`[data-agent="${ARCH}"] .needs`);

async function mount(s: ReturnType<typeof setup>) {
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
}
let keep = () => {};
afterEach(async () => {
  keep();
  keep = () => {};
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

describe('needs you', () => {
  it('shows the kind, the line and a way to the terminal in the chat, and the kind on the rail row', async () => {
    const s = setup();
    const fx = s.daemon.sessions.get(ARCH)!.fx;
    keep = () => void (fx.view = undefined); // the fixture is shared by every setup
    fx.view = {
      needs_you: { kind: 'usage', since: new Date(Date.now() - 5 * 60_000).toISOString(), text: 'Weekly limit reached', source: 'session' },
    };
    await mount(s);
    await until(() => card(), 'the card');
    expect(card()!.textContent).toContain('Hit the session limit');
    expect(card()!.textContent).toContain('Weekly limit reached');
    expect(card()!.querySelector('button')!.getAttribute('aria-label')).toBe('Open terminal');
    expect(chip()?.textContent).toBe('usage');
  });

  it('shows no card and no chip when the daemon says nothing is waited on', async () => {
    const s = setup();
    await mount(s);
    await wait(300);
    expect(card()).toBeNull();
    expect(chip()).toBeNull();
  });

  it('leaves the answer to the prompt card while a prompt is open', async () => {
    const s = setup();
    const a = s.daemon.sessions.get(ARCH)!;
    keep = () => void (a.fx.view = undefined);
    a.fx.view = { needs_you: { kind: 'permission', since: new Date().toISOString(), text: null, source: 'hook' } };
    (s.daemon as unknown as { script: { openPrompt(a: unknown): void } }).script.openPrompt(a);
    await mount(s);
    await wait(300);
    expect(card()).toBeNull();
  });
});
