// A note or a card's text shown open; a long one is cut to its first lines with a quiet Show more / Show less.
import { useState, type ReactNode } from 'react';

export function Fold({ long, children }: { long: boolean; children: ReactNode }) {
  const [more, setMore] = useState(false);
  return (
    <>
      <div className={long && !more ? 'line-clamp-8' : undefined}>{children}</div>
      {long ? (
        <button
          type="button"
          className="mt-0.5 cursor-pointer border-0 bg-transparent p-0 text-hint text-link"
          aria-expanded={more}
          onClick={() => setMore(!more)}
        >
          {more ? 'Show less' : 'Show more'}
        </button>
      ) : null}
    </>
  );
}
