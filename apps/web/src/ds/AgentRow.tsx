// An agent in the sidebar: a state glyph, the name, the waiting chip, a count and the time. The line under it says one thing, in this order:
// what it asks, the unread words, what it does with its task, why it waits.
import type { ReactNode } from 'react';
import { StateMark, type StateName } from '#src/ds/StateMark.tsx';

export function AgentRow({
  state,
  name,
  chip,
  time,
  line,
  hot,
  selected,
  onOpen,
}: {
  state: StateName;
  name: string;
  chip?: ReactNode;
  time?: string;
  line?: ReactNode;
  hot?: boolean;
  selected?: boolean;
  onOpen?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={selected ? 'true' : undefined}
      className={`flex w-full max-w-60 cursor-pointer flex-col rounded-sm border-0 py-1.5 pr-3 pl-2 text-left focus-visible:outline-2 focus-visible:outline-acc ${selected ? 'bg-canvas' : 'bg-transparent hover:bg-panel'}`}
    >
      <span className="flex items-center gap-1.5 text-md font-medium">
        <StateMark state={state} />
        {name}
        {chip}
        <em className="ml-auto text-xs font-normal text-ink-3 not-italic">{time}</em>
      </span>
      {line ? <span className={`truncate pl-[21px] text-sm ${hot ? 'text-ink' : 'text-ink-3'}`}>{line}</span> : null}
    </button>
  );
}
