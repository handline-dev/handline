import { createRoot } from 'react-dom/client';
import { Start } from './Start.tsx';
import { browserStorage, daemonUrl } from './daemon.ts';
import './styles.css';

const { url, insecure } = daemonUrl(location.search, location, import.meta.env.PROD, browserStorage()?.getItem('shell:own-host') || null);
createRoot(document.getElementById('root')!).render(
  <Start url={url} insecure={insecure} failure={browserStorage()?.getItem('shell:daemon-failure') || null} storage={browserStorage()} />,
);
