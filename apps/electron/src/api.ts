// window.handline as the page sees it, made from the preload's ipcRenderer (docs/electron-bridge.md §2). Calls return a result and never throw.
import type { NavigateTo, ShellApi, ShellUpdate } from '@handline/app-core';
import { CHANNELS } from '#src/bridge.ts';

export interface Ipc {
  invoke(channel: string, arg: unknown): Promise<unknown>;
  on(channel: string, f: (event: unknown, arg: unknown) => void): unknown;
}

export function makeApi(ipc: Ipc, zoom: () => number = () => 1): ShellApi {
  // A click before the page registered is kept and delivered to the first listener.
  let waiting: NavigateTo | null = null;
  let listener: ((to: NavigateTo) => void) | null = null;
  ipc.on(CHANNELS.navigate, (_e, to) => {
    if (listener) listener(to as NavigateTo);
    else waiting = to as NavigateTo;
  });
  // The shell sends the state on every load and change; the last one is kept for the first listener.
  let full = false;
  const fullListeners = new Set<(full: boolean) => void>();
  ipc.on(CHANNELS.fullScreen, (_e, on) => {
    full = on === true;
    fullListeners.forEach((f) => f(full));
  });
  // The shell sends the update state on every change; the last one is kept for the first listener.
  let update: ShellUpdate | null = null;
  const updateListeners = new Set<(v: ShellUpdate) => void>();
  ipc.on(CHANNELS.update, (_e, v) => {
    update = v as ShellUpdate;
    updateListeners.forEach((f) => f(update!));
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
    zoom,
    screen: (s) => call(CHANNELS.screen, s),
    unsent: (on) => call(CHANNELS.unsent, on),
    openPath: (a) => call(CHANNELS.openPath, a),
    bringIn: (a) => call(CHANNELS.bringIn, a),
    compose: (a) => call(CHANNELS.compose, a ?? {}),
    cancelCompose: () => call(CHANNELS.cancelCompose),
    captureWindows: () => call(CHANNELS.captureWindows),
    captureWindow: (a) => call(CHANNELS.captureWindow, a),
    preview: (a) => call(CHANNELS.preview, a),
    capturePreview: () => call(CHANNELS.capturePreview),
    deviceShot: (a) => call(CHANNELS.deviceShot, a),
    deviceTap: (a) => call(CHANNELS.deviceTap, a),
    deviceList: () => call(CHANNELS.deviceList),
    deviceSwipe: (a) => call(CHANNELS.deviceSwipe, a),
    deviceText: (a) => call(CHANNELS.deviceText, a),
    mode: () => call(CHANNELS.mode),
    setMode: (m) => call(CHANNELS.setMode, m),
    follow: () => call(CHANNELS.follow),
    checkUpdate: () => call(CHANNELS.checkUpdate),
    build: () => call(CHANNELS.build),
    restartDaemon: () => call(CHANNELS.restartDaemon),
    update: () => call(CHANNELS.update),
    updateAct: (what) => call(CHANNELS.updateAct, what),
    onUpdate(cb) {
      updateListeners.add(cb);
      if (update) cb(update);
      return () => void updateListeners.delete(cb);
    },
    cli: () => call(CHANNELS.cli),
    linkCli: () => call(CHANNELS.linkCli),
    phone: () => call(CHANNELS.phone),
    pairRemote: (a) => call(CHANNELS.pairRemote, a),
    onFullScreen(cb) {
      fullListeners.add(cb);
      cb(full);
      return () => void fullListeners.delete(cb);
    },
    settingsGet: (a) => call(CHANNELS.settingsGet, a ?? {}),
    settingsSet: (a) => call(CHANNELS.settingsSet, a),
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
