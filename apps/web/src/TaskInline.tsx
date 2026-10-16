// Fields of the task detail that are edited where they are: a click turns the value into its input, Enter or leaving it saves, Escape puts it back.
import { useRef, useState, type ReactNode } from 'react';
import type { TaskDetail as Detail } from '@handline/client';
import { isPerson } from '@handline/app-core';
import { useStore } from '@handline/app-react';
import { classify, sentence, useWrite } from '@handline/app-data';
import { BodyEditor } from './BodyEditor.tsx';
import { useCan } from './taskui.tsx';
import { Button, Input, NativeSelect } from './ui/index.ts';

export type Change = { title?: string; body?: string; owner?: string; time_box?: string; priority?: 1 | 2 | 3 };

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

const LOOK = 'cursor-text rounded-ctl border-0 bg-transparent p-0 text-left text-inherit hover:bg-raised hover:outline hover:outline-1 hover:outline-line';

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
  return (
    <button type="button" className={LOOK} title={`Click to edit the ${label.toLowerCase()}`} aria-label={`Edit ${label}`} onClick={() => setOn(true)}>
      {children}
    </button>
  );
}

/** One line of text: Enter or leaving the field saves a changed value, Escape leaves it as it was. */
export function TextInput({ value, label, onSave, onClose }: { value: string; label: string; onSave: (v: string) => void; onClose: () => void }) {
  const [text, setText] = useState(value);
  const left = useRef(false); // Escape closes first; the blur that follows must not save
  const finish = (keep: boolean) => {
    if (left.current) return;
    left.current = true;
    if (keep && text.trim() && text.trim() !== value) onSave(text.trim());
    onClose();
  };
  return (
    <Input
      autoFocus
      dense
      className="tinput min-w-0"
      value={text}
      aria-label={label}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') finish(true);
        else if (e.key === 'Escape') {
          e.stopPropagation();
          finish(false);
        }
      }}
    />
  );
}

export function PriorityInput({ value, onSave, onClose }: { value: 1 | 2 | 3; onSave: (v: 1 | 2 | 3) => void; onClose: () => void }) {
  return (
    <NativeSelect
      autoFocus
      value={value}
      aria-label="Priority"
      onChange={(e) => (onSave(Number(e.target.value) as 1 | 2 | 3), onClose())}
      onBlur={onClose}
      onKeyDown={(e) => e.key === 'Escape' && (e.stopPropagation(), onClose())}
    >
      <option value={1}>P1 high</option>
      <option value={2}>P2</option>
      <option value={3}>P3 low</option>
    </NativeSelect>
  );
}

/** The description, edited rich in place with Save and Cancel under it. */
export function BodyInline({ t, onClose }: { t: Detail; onClose: () => void }) {
  const edit = useEditTask(t);
  const [body, setBody] = useState(t.body);
  const [busy, setBusy] = useState(false);
  const save = () => (body === t.body ? onClose() : edit.save({ body }, onClose));
  return (
    <div className="grid gap-2" onKeyDown={(e) => e.key === 'Escape' && (e.stopPropagation(), onClose())}>
      <BodyEditor value={body} onChange={setBody} onBusy={setBusy} label="Body" />
      <div className="flex items-center gap-2">
        <Button variant="solid" label="Save" disabled={busy || edit.pending} onClick={save}>
          Save
        </Button>
        <Button label="Cancel" onClick={onClose}>
          Cancel
        </Button>
        {edit.error ? <span className="text-bad" role="alert">{edit.error}</span> : null}
      </div>
    </div>
  );
}

/** An agent's name opens that agent's chat; the person's own name and `human` are only text. */
export function AgentLink({ name, className = '' }: { name: string; className?: string }) {
  const me = useStore().me;
  if (!name || isPerson(name, me)) return <>{name}</>;
  return (
    <a href={`#/agents/${encodeURIComponent(name)}`} className={`text-inherit no-underline hover:underline ${className}`} onClick={(e) => e.stopPropagation()}>
      {name}
    </a>
  );
}
