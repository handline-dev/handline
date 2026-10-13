// The one way into the debug window: a quiet button at the very bottom of Settings that opens it in a window of its own.
import { Button } from '../ui/index.ts';
import { DEBUG_HASH } from './paneFacts.ts';

/** The debug window's address: this page with the debug route. */
export const debugUrl = (here: Pick<Location, 'origin' | 'pathname' | 'search'>): string => `${here.origin}${here.pathname}${here.search}${DEBUG_HASH}`;

export function DebugEntry({ open = (url: string) => window.open(url, 'handline-debug-terminals', 'width=1280,height=820') }) {
  return (
    <footer className="settings-debug mt-8 flex justify-center">
      <Button variant="ghost" size="sm" label="Debug: terminals" onClick={() => void open(debugUrl(location))} />
    </footer>
  );
}
