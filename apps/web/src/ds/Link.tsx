// Text that names a thing opens it: a link (and the "see it in full" link at a row's end), a task ID, an agent's name.
// With an href it is an anchor, otherwise a button; either way it takes the keyboard.
import { ArrowRight } from 'lucide-react';
import type { ComponentProps, ReactNode } from 'react';

type Press = { href?: string; onClick?: () => void; className?: string; children: ReactNode; title?: string };

function Pressable({ href, onClick, className, children, title }: Press) {
  const cls = `cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-acc tint ${className ?? ''}`;
  if (href)
    return (
      <a href={href} onClick={onClick} className={cls} title={title}>
        {children}
      </a>
    );
  return (
    <button type="button" onClick={onClick} className={`${cls} border-0 bg-transparent p-0 text-left font-[inherit]`} title={title}>
      {children}
    </button>
  );
}

/** A PR number, a source, or any named thing in running text. */
export function Link(p: Press) {
  return <Pressable {...p} className={`inline-flex items-center gap-[3px] text-acc-ink ${p.className ?? ''}`} />;
}

/** A task ID, set in the mono face. */
export function Id(p: Press) {
  return <Pressable {...p} className={`font-mono text-[0.92em] text-acc-ink ${p.className ?? ''}`} />;
}

/** An agent's name: bold, opens that agent's chat. */
export function Who(p: Press) {
  return <Pressable {...p} className={`font-semibold text-ink! ${p.className ?? ''}`} />;
}

/** The link at the right end of an action row: where to see the thing in full ("View in chat"). */
export function SeeInFull({ children, ...p }: Omit<ComponentProps<typeof Link>, 'className'>) {
  return (
    <Link {...p} className="ml-auto text-sm">
      {children}
      <ArrowRight width={13} height={13} strokeWidth={1.75} aria-hidden />
    </Link>
  );
}
