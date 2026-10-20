import type { KV } from '@handline/app-core';
import { setting } from '#src/daemon.ts';

export type Theme = 'system' | 'dark' | 'light';

/** The viewer's theme: `system` leaves <html data-theme> off so the OS decides (styles tokens.css), the others force it. */
export const themeOf = (storage: KV | null): Theme => {
  const v = setting(storage, 'theme', 'system').value;
  return v === 'dark' || v === 'light' ? v : 'system';
};

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === 'system') delete root.dataset.theme;
  else root.dataset.theme = theme;
}
