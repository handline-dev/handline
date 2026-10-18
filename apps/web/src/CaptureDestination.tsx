// Where a capture goes, as one chip in the bottom bar: "T123" (a comment on that task) or "New task → agent". Its popover changes either.
import type { ReactNode } from 'react';
import { taskRef } from '@handline/app-core';
import { Button, Select } from './ui/index.ts';

export interface Destination {
  as: 'task' | 'comment';
  target: number | null;
  owner: string;
}

export function CaptureDestination({
  value,
  onChange,
  open,
  onOpen,
  tasks,
  agents,
}: {
  value: Destination;
  onChange: (d: Destination) => void;
  open: boolean;
  onOpen: (open: boolean) => void;
  tasks: { id: number; title: string }[];
  agents: string[];
}): ReactNode {
  const comment = value.as === 'comment';
  const words = comment ? `Comment on ${value.target === null ? '…' : taskRef(value.target)}` : `New task → ${value.owner || '…'}`;
  return (
    <span
      className="relative"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.stopPropagation();
          onOpen(false);
        }
      }}
    >
      <Button icon={comment ? 'chat' : 'tasks'} label={words} aria-expanded={open} onClick={() => onOpen(!open)}>
        {words}
      </Button>
      {open ? (
        <div className="absolute bottom-full left-0 z-10 mb-1 grid w-64 gap-2 rounded-card border border-line bg-panel p-2 shadow-[var(--shadow-pop)]">
          <Select
            label="Send as"
            value={value.as}
            onChange={(e) => onChange({ ...value, as: e.target.value as Destination['as'] })}
            options={[
              { value: 'task', label: 'A new task' },
              { value: 'comment', label: 'A comment on a task' },
            ]}
          />
          {comment ? (
            <Select
              label="Task"
              value={value.target === null ? '' : String(value.target)}
              onChange={(e) => onChange({ ...value, target: e.target.value ? Number(e.target.value) : null })}
              options={[
                { value: '', label: 'pick a task' },
                ...tasks.map((t) => ({ value: String(t.id), label: `${taskRef(t.id)} ${t.title}` })),
              ]}
            />
          ) : (
            <Select
              label="Owner"
              value={value.owner}
              onChange={(e) => onChange({ ...value, owner: e.target.value })}
              options={[{ value: '', label: 'pick an agent' }, ...agents.map((a) => ({ value: a, label: a }))]}
            />
          )}
        </div>
      ) : null}
    </span>
  );
}
