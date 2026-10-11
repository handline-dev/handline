// The one page container (G07): a form or settings page is a centred column of 640 px, a list or a table takes the full width.
import type { ReactNode } from 'react';

export function Page({ width = 'form', children }: { width?: 'form' | 'wide'; children: ReactNode }) {
  return <div className={`ui-page mx-auto p-4${width === 'form' ? ' max-w-[var(--w-form)]' : ''}`}>{children}</div>;
}
