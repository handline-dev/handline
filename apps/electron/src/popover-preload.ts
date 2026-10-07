// The popover's only power: two buttons, each a message to the main process.
import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('followMain', {
  restart: () => void ipcRenderer.send('handline:follow-restart'),
  later: () => void ipcRenderer.send('handline:follow-later'),
});
