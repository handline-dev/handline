import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { DaemonProvider } from '@handline/app-react';
import { DataProvider, useAgentDetail, type BoxComponent } from '@handline/app-data';
import { setup } from '@handline/fake-daemon/helpers';
import { TerminalTab } from '../src/TerminalTab.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const Box: BoxComponent = (p) => <div {...p} />;
const ARCH = 'handline.architect'; // idle with a session and a terminal
const REFUSAL = 'its screen could not be read';

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
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});

function Probe() {
  const d = useAgentDetail(ARCH).data;
  return d ? <TerminalTab agent={ARCH} detail={d} /> : null;
}
/** The terminal tab with the keyboard taken and session.input refused as the daemon does for an unread screen; types one line. */
async function typeLine(daemon?: { bytes?: boolean }) {
  const s = setup({ daemon });
  const store = new Store(s.client);
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test" box={Box}>
          <Probe />
        </DataProvider>
      </DaemonProvider>,
    );
  });
  const input = () => box.querySelector<HTMLTextAreaElement>('textarea.grid-input');
  await until(input, 'the tab');
  await act(async () => void (await s.client.takeInput(ARCH)));
  s.daemon.knobs.refuse('session.input', 'screen_blocker', REFUSAL, 'use session.keys or prompt.answer');
  await until(() => !box.querySelector('.grid-status')?.textContent, 'the tab to attach');
  const el = input()!;
  await until(() => ![...box.querySelectorAll('button')].some((b) => b.textContent?.includes('Take control')), 'the keyboard');
  await act(async () => {
    el.value = 'run the tests';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  return { s, el, status: () => box.querySelector('.grid-status')!.textContent };
}

describe('typing in the terminal tab', () => {
  it('types as bytes when the daemon serves them, so a refused session.input does not stop it', async () => {
    const t = await typeLine();
    await until(
      () => t.s.daemon.sessions.get(ARCH)!.received.some((r) => r.kind === 'text' && r.value === 'run the tests'),
      'the typed text',
    );
    expect(t.status()).toBe('');
    t.s.client.close();
  });

  it('shows the daemon’s refusal in the status and keeps the text when only session.input is offered', async () => {
    const t = await typeLine({ bytes: false });
    await until(() => t.status()?.includes(REFUSAL), 'the refusal');
    expect(t.el.value).toBe('run the tests');
    t.s.client.close();
  });
});
