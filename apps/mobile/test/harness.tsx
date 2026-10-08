// What the screen tests share: mounting Home on the fake daemon, and looking things up by testID.
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach } from 'vitest';
import { Store } from '@handline/app-core';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import { setup } from '@handline/fake-daemon/helpers';
import { Home } from '../src/screens/Home.tsx';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

export const ARCH = 'handline.architect'; // idle with a session
export const sleep = (ms: number) => act(async () => void (await new Promise((r) => setTimeout(r, ms))));
export async function until(cond: () => unknown, what: string, ms = 4000) {
  for (let t = 0; t < ms; t += 10) {
    if (cond()) return;
    await sleep(10);
  }
  throw new Error(`timed out waiting for ${what}`);
}

let root: Root | null = null;
export let box: HTMLElement;
export const $ = (id: string) => box.querySelector<HTMLElement>(`[data-testid="${id}"]`);
export const text = () => box.textContent ?? '';
/** The height a row is styled with (the DOM has no layout here, so this reads the style both rows are given from the tokens). */
export const rowHeightOf = (el: Element | null) => (el ? getComputedStyle(el).height : undefined);
export const press = (el: HTMLElement | null) => act(async () => void el?.click());

/** `role` is the caller's; `screen` replaces Home for a screen that Home does not reach yet. */
export async function mount(
  prepare: (s: ReturnType<typeof setup>) => void = () => {},
  daemon?: { bytes?: boolean },
  client?: string,
  as: { role?: NonNullable<Parameters<typeof setup>[0]>['role']; screen?: (store: Store) => ReactNode } = {},
) {
  const s = setup({ daemon, client, role: as.role });
  prepare(s);
  const store = new Store(s.client);
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test">{as.screen ? as.screen(store) : <Home store={store} onUnpair={() => {}} />}</DataProvider>
      </DaemonProvider>,
    );
  });
  return { ...s, store };
}

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  box?.remove();
});
