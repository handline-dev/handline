// Fields of the task detail that are edited where they are: a click turns the value into its input, Enter or leaving it saves, Escape puts it back.
import { useRef, useState, type ReactNode } from 'react';
import type { TaskDetail as Detail } from '@handline/client';
import { isPerson } from '@handline/app-core';
import { useMe } from '@handline/app-react';
import { classify, sentence, useWrite } from '@handline/app-data';
import { BodyEditor } from '#src/BodyEditor.tsx';
import { useCan } from '#src/taskui.tsx';
import { Button, Input } from '#src/ui/index.ts';

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

const LOOK = 'cursor-text rounded-ctl border-0 bg-transparent p-0 text-left text-inherit tint';

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
}: {
  value: string;
  label: string;
  onSave: (v: string) => void;
  onClose: () => void;
}) {
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

/** The description, edited rich in place with Save and Cancel under it. */
export function BodyInline({ t, onClose }: { t: Detail; onClose: () => void }) {
  const edit = useEditTask(t);
  const [body, setBody] = useState(t.body);
  const [busy, setBusy] = useState(false);
  const uploading = useRef(false); // a Save in the tick of a paste must already see the upload
  const save = () => (uploading.current ? undefined : body === t.body ? onClose() : edit.save({ body }, onClose));
  return (
    <div className="grid gap-2" onKeyDown={(e) => e.key === 'Escape' && (e.stopPropagation(), onClose())}>
      <BodyEditor value={body} onChange={setBody} onBusy={(b) => ((uploading.current = b), setBusy(b))} label="Body" />
      <div className="flex items-center gap-2">
        <Button variant="solid" label="Save" disabled={busy || edit.pending} onClick={save}>
          Save
        </Button>
        <Button label="Cancel" onClick={onClose}>
          Cancel
        </Button>
        {edit.error ? (
          <span className="text-bad" role="alert">
            {edit.error}
          </span>
        ) : null}
      </div>
    </div>
  );
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
