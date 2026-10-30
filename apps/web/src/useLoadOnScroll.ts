// Older rows load when the end of the list scrolls into view, so a list has no "Show older" button; a window without IntersectionObserver keeps the button.
import { useEffect, useRef } from 'react';

/** A ref for the sentinel under the list; `supported` is false where the button has to stay. */
export function useLoadOnScroll(more: boolean, loading: boolean, load: () => void) {
  const end = useRef<HTMLDivElement>(null);
  const supported = typeof IntersectionObserver !== 'undefined';
  useEffect(() => {
    const el = end.current;
    if (!supported || !more || loading || !el) return;
    const io = new IntersectionObserver((hits) => hits.some((h) => h.isIntersecting) && load());
    io.observe(el);
    return () => io.disconnect();
  }, [supported, more, loading, load]);
  return { end, supported };
}
