// Opt-in log of every source that can resize a terminal: set localStorage['handline.trace.resize'] = '1' and reload.
// Entries go to console.debug and to window.__resizeTrace, each stamped in ms since the page loaded.
const on = (): boolean => {
  try {
    return globalThis.localStorage?.getItem('handline.trace.resize') === '1';
  } catch {
    return false;
  }
};

export type ResizeSource = 'observer' | 'fit' | 'font' | 'fonts-loaded' | 'ask' | 'host';

export function traceResize(source: ResizeSource, detail: string): void {
  if (!on()) return;
  const line = `${Math.round(performance.now())}ms ${source} ${detail}`;
  const w = globalThis as { __resizeTrace?: string[] };
  (w.__resizeTrace ??= []).push(line);
  console.debug('[resize]', line);
}

if (on()) globalThis.document?.fonts?.addEventListener('loadingdone', () => traceResize('fonts-loaded', ''));
