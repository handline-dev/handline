// The frame of a long page: a contents list at the left (sticky, marks the group in view), the content 640 wide beside it, flush left.
import { useEffect, useRef, useState, type ReactNode } from 'react';

export type Part = { id: string; label: string };

/** The part whose section is the first one in view while the page scrolls. */
function useInView(parts: readonly Part[]): string {
  const [on, setOn] = useState(parts[0]?.id ?? '');
  const watch = useRef<IntersectionObserver | null>(null);
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const seen = new Set<string>();
    watch.current = new IntersectionObserver(
      (hits) => {
        for (const h of hits) {
          if (h.isIntersecting) seen.add(h.target.id);
          else seen.delete(h.target.id);
        }
        const first = parts.find((p) => seen.has(p.id));
        if (first) setOn(first.id);
      },
      { rootMargin: '0px 0px -70% 0px' },
    );
    return () => {
      watch.current?.disconnect();
      watch.current = null;
    };
  }, [parts]);
  // Sections can mount after the page does (a settings page waits for its read); observing one already watched changes nothing.
  useEffect(() => {
    for (const p of parts) {
      const el = document.getElementById(p.id);
      if (el) watch.current?.observe(el);
    }
  });
  return on;
}

export function ContentsPage({ parts, children }: { parts: readonly Part[]; children: ReactNode }) {
  const on = useInView(parts);
  return (
    <div className="contents-page flex min-w-0 gap-10 max-md:block">
      <nav
        aria-label="Contents"
        className="sticky top-0 flex max-h-screen w-[200px] flex-none flex-col gap-0.5 self-start py-2 max-md:hidden"
      >
        {parts.map((p) => (
          <button
            key={p.id}
            type="button"
            aria-current={p.id === on ? 'location' : undefined}
            className={`tint cursor-pointer rounded-sm border-0 bg-transparent px-2 py-1 text-left text-md ${p.id === on ? 'bg-raised font-medium text-ink' : 'text-ink-2'}`}
            onClick={() => document.getElementById(p.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
          >
            {p.label}
          </button>
        ))}
      </nav>
      <div className="flex min-w-0 max-w-[640px] flex-1 flex-col gap-4">{children}</div>
    </div>
  );
}
