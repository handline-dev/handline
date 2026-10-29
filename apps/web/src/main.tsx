import { createRoot } from 'react-dom/client';
import { Start } from '#src/Start.tsx';
import { applyTheme, themeOf } from '#src/appearance.ts';
import { browserStorage, daemonUrl } from '#src/daemon.ts';
import { inShell, trackShellInsets } from '#src/shell.ts';
import '#src/styles.css';

// The chrome reads this in the shell (styles/base.css).
if (inShell()) {
  document.documentElement.dataset.shell = 'desktop';
  trackShellInsets();
}

applyTheme(themeOf(browserStorage()));

const { url, insecure } = daemonUrl(location.search, location, import.meta.env.PROD, browserStorage()?.getItem('shell:own-host') || null);
createRoot(document.getElementById('root')!).render(
  <Start url={url} insecure={insecure} failure={browserStorage()?.getItem('shell:daemon-failure') || null} storage={browserStorage()} />,
);
