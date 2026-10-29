// Chat and its composer: my bubble on the right, the agent's words with no ground, one composer for chat and comments.
import { ChevronRight } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode, TextareaHTMLAttributes } from 'react';

export function UserBubble({ children }: { children: ReactNode }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[70%] rounded-lg bg-acc px-3.5 py-2 text-on-acc">{children}</div>
    </div>
  );
}

/** The folded line of what the agent did in its turn. */
export function WorkLine({ children, onOpen }: { children: ReactNode; onOpen?: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="mt-4 mb-1.5 flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-sm text-ink-3 hover:text-ink-2 focus-visible:outline-2 focus-visible:outline-acc"
    >
      {children}
      <ChevronRight width={13} height={13} strokeWidth={1.75} aria-hidden />
    </button>
  );
}

export const AgentSay = ({ children }: { children: ReactNode }) => <div className="leading-[1.65]">{children}</div>;

export function Composer({ left, tools, ...rest }: { left?: ReactNode; tools?: ReactNode } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <div className="max-w-[680px] rounded-lg bg-surface px-4 py-3">
      <textarea
        {...rest}
        rows={rest.rows ?? 1}
        aria-label={rest['aria-label'] ?? rest.placeholder}
        className="block w-full resize-none border-0 bg-transparent p-0 font-sans text-md text-ink outline-none placeholder:text-ink-3"
      />
      <div className="mt-3 flex items-center gap-2 text-sm text-ink-2">
        <span className="ml-auto flex items-center gap-1">{left}</span>
        {tools}
      </div>
    </div>
  );
}

export function ComposerFoot({ children }: { children: ReactNode }) {
  return <div className="flex max-w-[680px] items-center gap-4 px-4 pt-1.5 text-sm text-ink-2">{children}</div>;
}

export function FootCell({ icon: Glyph, onOpen, children }: { icon: LucideIcon; onOpen?: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="inline-flex cursor-pointer items-center gap-1 rounded-sm border-0 bg-transparent px-1 py-0.5 text-inherit hover:bg-panel focus-visible:outline-2 focus-visible:outline-acc"
    >
      <Glyph width={13} height={13} strokeWidth={1.75} aria-hidden />
      {children}
    </button>
  );
}
