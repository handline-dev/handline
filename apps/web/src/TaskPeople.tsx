// Handing a task to someone, with what to tell them.
import { useState } from 'react';
import { assignGroups, taskRef } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { useAgents, useChatActions, useTeam, useWrite } from '@handline/app-data';
import { Textarea } from './ui/index.ts';
import { Sheet, useCan } from './taskui.tsx';

export const CHIP = 'rounded-full border px-2 py-0.5 text-label';
export const chipLook = (on: boolean) =>
  on ? 'border-primary bg-primary text-on-primary' : 'border-line-strong bg-transparent text-ink hover:bg-raised';

/** Who the task can go to, in three labelled groups; one is picked, then what to say with it. */
export function AssignSheet({ t, me, onClose }: { t: Detail; me: string; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const chat = useChatActions(t.to);
  const known = useAgents().data?.agents.map((a) => a.name) ?? [];
  const team = useTeam().data ?? { agents: [] };
  const groups = assignGroups(t, team, known, me);
  const [to, setTo] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const submit = () => {
    if (!to) return;
    const text = note.trim();
    edit.mutate(
      { task: t.id, owner: to },
      {
        onSuccess: () => {
          if (text) chat.send(`Task ${taskRef(t.id)} (${t.title}) is yours now. ${text}`, { to });
          onClose();
        },
      },
    );
  };
  return (
    <Sheet
      label={`Assign ${taskRef(t.id)} to…`}
      submit={to ? `Assign to ${to}` : 'Pick someone'}
      onCancel={onClose}
      onSubmit={submit}
      busy={edit.isPending}
    >
      <div className="grid gap-2">
        {groups.length ? (
          groups.map((g) => (
            <section key={g.key} aria-label={g.label}>
              <div className="mb-1 text-label text-ink-3">{g.label}</div>
              <div className="flex flex-wrap gap-1.5">
                {g.names.map((n) => (
                  <button
                    key={n}
                    type="button"
                    aria-pressed={to === n}
                    className={`${CHIP} ${chipLook(to === n)}`}
                    onClick={() => setTo(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </section>
          ))
        ) : (
          <span className="muted">There is no one else to assign it to.</span>
        )}
        <Textarea
          grow
          className="tinput"
          rows={3}
          value={note}
          aria-label="What to tell them"
          placeholder={to ? `What to tell ${to} (optional)` : 'What to tell them (optional)'}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
    </Sheet>
  );
}
