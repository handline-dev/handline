// Runs in the sandboxed window before the page: the only thing the page gets from the shell is window.handline, and the device token
// the shell paired for it, put where the page keeps one (the page reads it from its own storage, as after a manual pairing).
import { contextBridge, ipcRenderer } from 'electron';
import { makeApi } from './api.ts';
import { CHANNELS } from './bridge.ts';

contextBridge.exposeInMainWorld('handline', makeApi(ipcRenderer));
try {
  const token = ipcRenderer.sendSync(CHANNELS.token) as string | null;
  const daemon = new URLSearchParams(location.search).get('daemon');
  if (token && daemon) localStorage.setItem(`token:${daemon}`, token);
} catch {
  /* no token: the page shows its pairing screen */
}
