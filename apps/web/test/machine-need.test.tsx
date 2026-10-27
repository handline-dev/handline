// The machine's own needs-you item (daemon.status needs_you): a card with its text and since, no buttons, gone when the daemon drops it.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it } from 'vitest';
import { Store } from '@handline/app-core';
import { setMachineNeeds, setup } from '@handline/fake-daemon/helpers';
import { App } from '#src/App.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
  location.hash = '';
});
const machine = () => box.querySelector<HTMLElement>('.my-turn-card[data-kind="machine"]');

it('shows a full disk as a card with its text and age and no button, and drops it when the daemon does', async () => {
  const s = setup();
  s.daemon.admin.machineNeeds = [
    {
      kind: 'disk',
      since: new Date(Date.now() - 12 * 60_000).toISOString(),
      text: 'Free disk is 3.1 GB, under the 5 GB floor: builds and tests wait.',
    },
  ];
  location.hash = '#/my-turn';
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(<App store={new Store(s.client)} daemon="test" storage={null} />);
  });
  await until(machine, 'the disk card');
  expect(machine()!.textContent).toContain('Free disk is 3.1 GB, under the 5 GB floor');
  expect(machine()!.textContent).toMatch(/waiting 12m/);
  expect(machine()!.querySelectorAll('button')).toHaveLength(0);
  await setMachineNeeds(s, []);
  await until(() => !machine(), 'the card to go');
});
