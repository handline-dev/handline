// The shell's openIn and openInApps calls wired to the machine's own pieces.
import { execFile } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import { ipcMain } from 'electron';
import type { DaemonClient } from '@handline/client';
import { CHANNELS } from '#src/bridge.ts';
import { OpenIn } from '#src/open-in.ts';

/** openIn and openInApps with the machine's own pieces: the shell's founder connection for the folder and the tmux command, and /usr/bin/open through execFile. */
export function handleOpenIn(origin: string, client: DaemonClient, tmp: string): void {
  const openIn = new OpenIn(origin, {
    platform: process.platform,
    home: os.homedir(),
    tmp,
    worktreeOf: async (agent) => (await client.call('agent.get', { agent })).worktree?.path ?? null,
    attachOf: async (agent) => (await client.call('terminal.open', { agent })).attach?.argv ?? null,
    exec: (file, args) => new Promise((resolve, reject) => execFile(file, args, (err) => (err ? reject(err) : resolve()))),
    exists: (p) =>
      fs.promises.access(p).then(
        () => true,
        () => false,
      ),
    later: (fn, ms) => void setTimeout(fn, ms).unref(),
  });
  ipcMain.handle(CHANNELS.openIn, (e, arg) => openIn.open(e, arg));
  ipcMain.handle(CHANNELS.openInApps, (e) => openIn.apps(e));
}
