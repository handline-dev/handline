// A detail is always a header, the why, then an answer row that ends with where to see it in full.
import { ShieldCheck } from 'lucide-react';
import type { ReactNode } from 'react';

export function ItemHeader({ source, who, when, title }: { source: ReactNode; who?: ReactNode; when: string; title: ReactNode }) {
  return (
    <header>
      <div className="flex items-center gap-2 text-sm text-ink-3">
        {source}
        {who}
        <span>· {when}</span>
      </div>
      <h2 className="mt-1 text-lg font-semibold">{title}</h2>
    </header>
  );
}

/** One hairline sets the answer apart. `code` prints what is asked in the mono face; `children` are the buttons, `full` the link at the end. */
export function Answer({
  kind,
  ask,
  code,
  full,
  children,
}: {
  kind: string;
  ask: ReactNode;
  code?: boolean;
  full?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-4 border-t border-line pt-3">
      <div className="flex items-center gap-[5px] text-sm text-ink-2">
        <ShieldCheck width={14} height={14} strokeWidth={1.75} aria-hidden />
        {kind}
      </div>
      <div className={code ? 'mt-1.5 mb-3 rounded-sm bg-surface px-3 py-2 font-mono text-sm' : 'mt-1.5 mb-3'}>{ask}</div>
      <div className="flex items-center gap-2">
        {children}
        {full}
      </div>
    </section>
  );
}
