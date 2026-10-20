import { useEffect } from 'react';

/** Calls `onEscape` when Escape is pressed anywhere in the window, while the caller is mounted. */
export function useEscape(onEscape: () => void): void {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onEscape();
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onEscape]);
}
