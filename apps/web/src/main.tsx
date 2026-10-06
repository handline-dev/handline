import { createRoot } from 'react-dom/client';
import { Store } from '@handline/app-core';
import { DaemonClient, webSocketTransport } from '@handline/client';
import { App } from './App.tsx';
import { browserStorage, daemonUrl } from './daemon.ts';
import './styles.css';

const { url, insecure } = daemonUrl(location.search, location);
const store = new Store(new DaemonClient({ client: 'web-pc', connect: () => webSocketTransport(url) }));
if (insecure) store.fail(`Refusing ${url}: use wss:// for a daemon that is not on this machine.`);
createRoot(document.getElementById('root')!).render(<App store={store} daemon={url} storage={browserStorage()} connect={!insecure} />);
