// What the screen tests share: mounting Home on the fake daemon, and looking things up by testID.
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach } from 'vitest';
import { Store } from '@handline/app-core';
import { DataProvider } from '@handline/app-data';
import { DaemonProvider } from '@handline/app-react';
import { setup } from '@handline/fake-daemon/helpers';
import type { FakeDaemonOptions } from '@handline/fake-daemon/daemon';
import { Home } from '#src/screens/Home.tsx';

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
// A Modal draws outside the screen's own container, so the page is read when the container has none.
export const $ = (id: string) =>
  box.querySelector<HTMLElement>(`[data-testid="${id}"]`) ?? document.body.querySelector<HTMLElement>(`[data-testid="${id}"]`);
/** The text of the page and any sheet over it, or of one element. */
export const text = (el?: Element | null) => (el ?? document.body).textContent ?? '';
/** The height a row is styled with (the DOM has no layout here, so this reads the style both rows are given from the tokens). */
export const rowHeightOf = (el: Element | null) => (el ? getComputedStyle(el).height : undefined);
/** Types into a field the way a keyboard does (react-native-web draws TextInput as an input or a textarea). */
export const fill = (id: string, v: string) =>
  act(async () => {
    const f = $(id) as unknown as HTMLInputElement | HTMLTextAreaElement;
    const proto = f.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(f, v);
    f.dispatchEvent(new Event('input', { bubbles: true }));
  });
export const value = (id: string) => ($(id) as unknown as HTMLInputElement | null)?.value;
export const press = (el: HTMLElement | null) => act(async () => void el?.click());

/** Opens the agent page's menu and picks an item in it. */
export async function pickItem(id: string) {
  await until(() => $('verbs-open'), 'the page');
  await press($('verbs-open'));
  if (!$(id)) await press($('verb-advanced'));
  await until(() => $(id), `the ${id} item`);
  await press($(id));
}
/** A view of the agent page: Chat is the page itself (back returns to it); the others are rows of the ... menu. */
export const pickTab = async (id: string) => {
  if (id === 'chat') {
    if ($('agent-view')) await press($('back'));
    await until(() => $('chat-input') || $('chat-off'), 'the chat');
    return;
  }
  await until(() => $('verbs-open'), 'the menu button');
  await press($('verbs-open'));
  const row = id === 'file' ? 'menu-files' : `menu-${id}`;
  await until(() => $(row), `the ${id} row`);
  await press($(row));
};

/** What a view's row in the ... menu says (the menu stays open). */
export async function tabLabel(id: string) {
  await until(() => $('verbs-open'), 'the menu button');
  await press($('verbs-open'));
  await until(() => $(`menu-${id}`), `the ${id} row`);
  return $(`menu-${id}`)!.textContent;
}

/** A task verb's button: the verbs sit under the status and More menus, so this opens whichever holds it. */
export async function verb(id: string): Promise<HTMLElement | null> {
  for (const m of ['status-menu', 'status-menu', 'more-menu', 'more-menu']) {
    if ($(`verb-${id}`)) break;
    await press($(m));
  }
  return $(`verb-${id}`);
}

/** `role` is the caller's; `screen` replaces Home for a screen that Home does not reach yet. */
export async function mount(
  prepare: (s: ReturnType<typeof setup>) => void = () => {},
  daemon?: FakeDaemonOptions,
  client?: string,
  as: { role?: NonNullable<Parameters<typeof setup>[0]>['role']; screen?: (store: Store) => ReactNode; opens?: 'auto' } = {},
) {
  const s = setup({ daemon, client, role: as.role });
  prepare(s);
  const store = new Store(s.client);
  box = document.body.appendChild(document.createElement('div'));
  root = createRoot(box);
  await act(async () => {
    root!.render(
      <DaemonProvider store={store}>
        <DataProvider daemonId="test">
          {as.screen ? as.screen(store) : <Home store={store} onUnpair={() => {}} startOn={as.opens === 'auto' ? undefined : 'agents'} />}
        </DataProvider>
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
