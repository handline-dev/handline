// The permission card: title on top, the command at full width (folded when long, with a toggle), the buttons below.
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
const text = (el: Element | null) => el?.textContent ?? '';

async function mount(command: string, s = setup()) {
  const prompt = {
    id: 'p9',
    agent: ARCH,
    kind: 'permission',
    tool: 'Bash',
    command,
    reason: null,
    lines: [],
    since: new Date().toISOString(),
    options: [
      { key: 'allow', label: 'Allow', answer: 'yes', highlighted: true },
      { key: 'deny', label: 'Deny', answer: 'no' },
    ],
  };
  s.daemon.sessions.get(ARCH)!.prompt = prompt as never;
  location.hash = `#/agents/${ARCH}`;
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(() => $('.prompt'), 'the card');
  return s;
}
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
  location.hash = '';
});
describe('the permission card', () => {
  it('shows a short command whole, with no fold toggle', async () => {
    await mount('git status');
    expect(text($('.prompt .cmd'))).toBe('git status');
    expect($('.prompt [aria-label="Show all"]')).toBeNull();
    expect(text($('.prompt'))).toContain('Allow');
  });

  it('folds a long command to a few lines and shows it all on Show all', async () => {
    await mount(Array.from({ length: 12 }, (_, i) => `echo ${i}`).join('\n'));
    expect($('.prompt .cmd')!.className).toContain('overflow-hidden');
    await act(async () => $('.prompt [aria-label="Show all"]')!.click());
    expect($('.prompt .cmd')!.className).not.toContain('overflow-hidden');
    expect($('.prompt [aria-label="Show less"]')).not.toBeNull();
  });
});
