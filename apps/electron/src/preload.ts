// Runs in the sandboxed window before the page: the only thing the page gets from the shell is window.handline, and the device token
// the shell paired for it, put where the page keeps one (the page reads it from its own storage, as after a manual pairing).
import { contextBridge, ipcRenderer } from 'electron';
import { makeApi } from './api.ts';
import { CHANNELS } from './bridge.ts';

contextBridge.exposeInMainWorld('handline', makeApi(ipcRenderer));
try {
  const given = ipcRenderer.sendSync(CHANNELS.token) as { token: string | null; ownHost: string | null } | null;
  const daemon = new URLSearchParams(location.search).get('daemon');
  if (given?.token && daemon) localStorage.setItem(`token:${daemon}`, given.token);
  // The one host the page may reach over plain ws besides loopback: this machine's own Tailscale address.
  localStorage.setItem('shell:own-host', given?.ownHost ?? '');
} catch {
  /* no token: the page shows its pairing screen */
}
