import { createRoot } from 'react-dom/client';
import { Start } from './Start.tsx';
import { browserStorage, daemonUrl } from './daemon.ts';
import { inShell } from './shell.ts';
import './styles.css';

// The chrome reads this in the shell (styles/base.css).
if (inShell()) document.documentElement.dataset.shell = 'desktop';

// Dev only: keys 1-4 try a preview theme (styles/themes.css), 0 back to the app's own.
if (import.meta.env.DEV) {
  const THEMES = ['linear', 'soft', 'color', 'paper'] as const;
  addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if ((e.target as HTMLElement | null)?.closest?.('input, textarea, select')) return;
    const root = document.documentElement;
    if (e.key === '0') return delete root.dataset.theme;
    const n = Number(e.key);
    if (n >= 1 && n <= THEMES.length) root.dataset.theme = THEMES[n - 1]!;
  });
}

const { url, insecure } = daemonUrl(location.search, location, import.meta.env.PROD, browserStorage()?.getItem('shell:own-host') || null);
createRoot(document.getElementById('root')!).render(
  <Start url={url} insecure={insecure} failure={browserStorage()?.getItem('shell:daemon-failure') || null} storage={browserStorage()} />,
);
