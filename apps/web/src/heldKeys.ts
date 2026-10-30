// Key hints show only while ⌘ (Ctrl off a Mac) is held: the window says so on <html data-keys="held">, and `.ui-keys` is shown only then (styles/ui.css).
import { useEffect } from 'react';

export function useHeldKeys() {
  useEffect(() => {
    const root = document.documentElement;
    const hold = (on: boolean) => (on ? (root.dataset.keys = 'held') : delete root.dataset.keys);
    const down = (e: KeyboardEvent) => hold(e.metaKey || e.ctrlKey);
    const up = (e: KeyboardEvent) => hold(e.metaKey || e.ctrlKey);
    const off = () => hold(false); // a ⌘ released in another window never sends a keyup here
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', off);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', off);
      off();
    };
  }, []);
}
