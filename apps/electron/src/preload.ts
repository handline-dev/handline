// Runs in the sandboxed window before the page: the only thing the page gets from the shell is window.handline.
import { contextBridge, ipcRenderer } from 'electron';
import { makeApi } from './api.ts';

contextBridge.exposeInMainWorld('handline', makeApi(ipcRenderer));
