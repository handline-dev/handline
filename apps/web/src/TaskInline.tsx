// Fields of the task detail that are edited where they are: a click turns the value into its input, Enter or leaving it saves, Escape puts it back.
import { useRef, useState, type ChangeEvent, type KeyboardEvent, type ReactNode } from 'react';
import type { TaskDetail as Detail } from '@handline/client';
import { isPerson } from '@handline/app-core';
import { useMe } from '@handline/app-react';
import { classify, sentence, useWrite } from '@handline/app-data';
import { useCan } from '#src/taskui.tsx';
import { BarInput, Input } from '#src/ui/index.ts';

export type Change = { title?: string; body?: string; evidence?: string; owner?: string; time_box?: string; priority?: 1 | 2 | 3 };

/** task.edit for this task, with the daemon's own words for a refusal. */
export function useEditTask(t: Detail) {
  const edit = useWrite('task.edit');
  const can = useCan('task.edit');
  return {
    can,
    error: edit.isError ? sentence(classify(edit.error)) : '',
    pending: edit.isPending,
    save: (change: Change, done?: () => void) => edit.mutate({ task: t.id, ...change }, { onSuccess: done }),
  };
}

const LOOK = 'cursor-text rounded-sm border-0 bg-transparent p-0 text-left text-inherit tint';

/** The value as it reads; a click opens `input(done)`, which draws the field and calls `done` when it is over. Without the right to edit it is only the value. */
export function Inline({
  can,
  label,
  children,
  input,
}: {
  can: boolean;
  label: string;
  children: ReactNode;
  input: (close: () => void) => ReactNode;
}) {
  const [on, setOn] = useState(false);
  if (!can) return <>{children}</>;
  if (on) return <>{input(() => setOn(false))}</>;
  // A div, not a button: a link or a picture inside the value keeps its own click.
  return (
    <div
      role="button"
      tabIndex={0}
      className={`${LOOK} inline-block max-w-full`}
      title={`Click to edit the ${label.toLowerCase()}`}
      aria-label={`Edit ${label}`}
      onClick={(e) => !(e.target as Element).closest('a, button') && setOn(true)}
      onKeyDown={(e) => {
        if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          setOn(true);
        }
      }}
    >
      {children}
    </div>
  );
}

/** One line of text: Enter or leaving the field saves a changed value, Escape leaves it as it was. */
export function TextInput({
  value,
  label,
  onSave,
  onClose,
  bar,
}: {
  value: string;
  label: string;
  onSave: (v: string) => void;
  onClose: () => void;
  /** The field sits in the top bar: the 32 px bar height, not the form field's. */
  bar?: boolean;
}) {
  const [text, setText] = useState(value);
  const left = useRef(false); // Escape closes first; the blur that follows must not save
  const finish = (keep: boolean) => {
    if (left.current) return;
    left.current = true;
    if (keep && text.trim() && text.trim() !== value) onSave(text.trim());
    onClose();
  };
  const props = {
    autoFocus: true,
    value: text,
    'aria-label': label,
    onChange: (e: ChangeEvent<HTMLInputElement>) => setText(e.target.value),
    onBlur: () => finish(true),
    onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') finish(true);
      else if (e.key === 'Escape') {
        e.stopPropagation();
        finish(false);
      }
    },
  };
  return bar ? <BarInput {...props} className="tinput w-full min-w-0" /> : <Input {...props} dense className="tinput min-w-0" />;
}

/** An agent's name opens that agent's chat; the person's own name and `human` are only text. */
export function AgentLink({ name, className = '' }: { name: string; className?: string }) {
  const me = useMe();
  if (!name || isPerson(name, me)) return <>{name}</>;
  return (
    <a
      href={`#/agents/${encodeURIComponent(name)}`}
      className={`text-inherit no-underline ${className}`}
      onClick={(e) => e.stopPropagation()}
    >
      {name}
    </a>
  );
}
