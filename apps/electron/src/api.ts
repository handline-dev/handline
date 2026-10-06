// window.handline as the page sees it, made from the preload's ipcRenderer (docs/electron-bridge.md §2). Calls return a result and never throw.
import type { NavigateTo, ShellApi } from '@handline/app-core';
import { CHANNELS } from './bridge.ts';

export interface Ipc {
  invoke(channel: string, arg: unknown): Promise<unknown>;
  on(channel: string, f: (event: unknown, arg: NavigateTo) => void): unknown;
}

export function makeApi(ipc: Ipc): ShellApi {
  // A click before the page registered is kept and delivered to the first listener.
  let waiting: NavigateTo | null = null;
  let listener: ((to: NavigateTo) => void) | null = null;
  ipc.on(CHANNELS.navigate, (_e, to) => {
    if (listener) listener(to);
    else waiting = to;
  });
  const call = async (channel: string, arg: unknown) => {
    try {
      return (await ipc.invoke(channel, arg)) as Awaited<ReturnType<ShellApi['screen']>>;
    } catch (e) {
      return { ok: false as const, code: 'bad_request' as const, message: e instanceof Error ? e.message : String(e) };
    }
  };
  return {
    api: 1,
    screen: (s) => call(CHANNELS.screen, s),
    unsent: (on) => call(CHANNELS.unsent, on),
    onNavigate(cb) {
      listener = cb;
      if (waiting) {
        const to = waiting;
        waiting = null;
        cb(to);
      }
      return () => {
        if (listener === cb) listener = null;
      };
    },
  };
}
