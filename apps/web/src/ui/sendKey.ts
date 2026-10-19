import type { KeyboardEvent } from 'react';

/** The one send key of every text box that sends: Enter sends, Shift+Enter breaks the line, and Enter while an IME is composing is the IME's. True when the key was the send key. */
export function sendKey(e: KeyboardEvent, send: () => void): boolean {
  if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return false;
  e.preventDefault();
  send();
  return true;
}
