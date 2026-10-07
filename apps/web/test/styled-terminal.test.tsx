// The text grid draws a styled attach's colour runs, and a daemon that serves text only gets the same rows with no colour.
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
async function mount(daemon?: { bytes?: boolean }) {
  const s = setup({ daemon });
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={new Store(s.client)}>
        <DataProvider daemonId="test" box={Box}>
          <Probe />
        </DataProvider>
      </DaemonProvider>,
    );
  });
  await until(() => box.querySelector('.grid')?.textContent, 'the first screen');
  return s;
}

describe('the terminal text grid', () => {
  it('draws the colour runs a styled attach carries, in the theme colours', async () => {
    const s = await mount();
    const grid = () => box.querySelector('.grid')!;
    s.daemon.daemonTypes(ARCH, 'a new line'); // the fake colours a '> ' prompt row
    await until(() => grid().textContent?.includes('> a new line'), 'the new line');
    const mark = [...grid().querySelectorAll<HTMLElement>('span[style]')].find((e) => e.textContent === '>');
    expect(mark, 'the prompt marker is a run of its own').toBeDefined();
    expect(mark!.style.cssText).toMatch(/color:\s*var\(--ansi-\d+\)/);
    s.client.close();
  });

  it('draws the same rows with no colour when the daemon serves text only', async () => {
    const s = await mount({ bytes: false });
    expect(box.querySelector('.grid')!.textContent).not.toBe('');
    expect(box.querySelector('.grid span[style]')).toBeNull();
    s.client.close();
  });
});
