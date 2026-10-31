import { useEffect } from 'react';

/** Runs `f` on a plain key press anywhere on the page except while a field has the focus. */
export function useKey(f: (key: string) => boolean) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || el?.closest('input,textarea,select,[contenteditable],[role=dialog]')) return;
      if (f(e.key.toLowerCase())) e.preventDefault();
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  });
}
