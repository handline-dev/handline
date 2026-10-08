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
  const call = async <R>(channel: string, arg?: unknown): Promise<R> => {
    try {
      return (await ipc.invoke(channel, arg)) as R;
    } catch (e) {
      return { ok: false, code: 'bad_request', message: e instanceof Error ? e.message : String(e) } as R;
    }
  };
  return {
    api: 1,
    screen: (s) => call(CHANNELS.screen, s),
    unsent: (on) => call(CHANNELS.unsent, on),
    openPath: (a) => call(CHANNELS.openPath, a),
    compose: (a) => call(CHANNELS.compose, a ?? {}),
    cancelCompose: () => call(CHANNELS.cancelCompose),
    mode: () => call(CHANNELS.mode),
    setMode: (m) => call(CHANNELS.setMode, m),
    pairRemote: (a) => call(CHANNELS.pairRemote, a),
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
