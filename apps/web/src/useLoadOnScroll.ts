// Older rows load when the end of the list scrolls into view, so a list has no "Show older" button; a window without IntersectionObserver keeps the button.
import { useEffect, useRef } from 'react';

/** A ref for the sentinel under the list; `supported` is false where the button has to stay. The observer lives while `loading` is unchanged, whatever `load` is. */
export function useLoadOnScroll(more: boolean, loading: boolean, load: () => void) {
  const end = useRef<HTMLDivElement>(null);
  const latest = useRef(load);
  useEffect(() => {
    latest.current = load;
  });
  const supported = typeof IntersectionObserver !== 'undefined';
  useEffect(() => {
    const el = end.current;
    if (!supported || !more || loading || !el) return;
    const io = new IntersectionObserver((hits) => hits.some((h) => h.isIntersecting) && latest.current());
    io.observe(el);
    return () => io.disconnect();
  }, [supported, more, loading]);
  return { end, supported };
}
