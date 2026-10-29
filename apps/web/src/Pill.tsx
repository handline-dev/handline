// A short line in the middle of the window that goes by itself and asks nothing.
import { useEffect, useState } from 'react';

const SHOW = 'handline:pill';
const LIVES_MS = 2200;

export const showPill = (text: string): void => void window.dispatchEvent(new CustomEvent(SHOW, { detail: text }));

export function PillHost() {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const on = (e: Event) => {
      setText((e as CustomEvent<string>).detail);
      clearTimeout(timer);
      timer = setTimeout(() => setText(null), LIVES_MS);
    };
    window.addEventListener(SHOW, on);
    return () => {
      window.removeEventListener(SHOW, on);
      clearTimeout(timer);
    };
  }, []);
  if (!text) return null;
  return (
    <div
      className="pill pointer-events-none fixed top-1/2 left-1/2 z-30 -translate-x-1/2 -translate-y-1/2 rounded-md bg-ink px-4 py-2 text-md text-canvas shadow-[var(--shadow)]"
      role="status"
    >
      {text}
    </div>
  );
}
