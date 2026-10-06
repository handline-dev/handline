import type { KV } from '@handline/app-core';
import { breakpoints } from '@handline/tokens';
import { setting } from '../daemon.ts';

/**
 * xterm when the daemon serves bytes, unless the viewer chose the text grid (setting `terminal`: auto | text) or is on a phone held
 * upright, where the text view with the key strip is the input (docs/terminal-pane.md §8).
 */
export function wantsBytes(formats: string[] | undefined, storage: KV | null): boolean {
  if (!formats?.includes('bytes') || setting(storage, 'terminal', 'auto').value === 'text') return false;
  return !matchMedia(`(max-width: ${breakpoints.phone - 1}px) and (orientation: portrait)`).matches;
}
