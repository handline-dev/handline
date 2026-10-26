// Handing a task to someone, with what to tell them.
import { useState } from 'react';
import { assignGroups, taskRef } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, sentence, useAgents, useTeam, useWrite } from '@handline/app-data';
import { Input } from '#src/ui/index.ts';
import { Sheet } from '#src/taskui.tsx';

export const CHIP = 'rounded-full border px-2 py-0.5 text-label';
export const chipLook = (on: boolean) =>
  on ? 'border-primary bg-primary text-on-primary' : 'border-line-strong bg-transparent text-ink hover:bg-raised';

/** The few who are likely (who worked on it, who is near in the team), a search over everyone; Enter or a click hands the task over at once. */
export function AssignSheet({ t, me, onClose }: { t: Detail; me: string; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const known = useAgents().data?.agents.map((a) => a.name) ?? [];
  const team = useTeam().data ?? { agents: [] };
  const groups = assignGroups(t, team, known, me);
  const [find, setFind] = useState('');
  const [at, setAt] = useState(0);
  const q = find.trim().toLowerCase();
  const all = groups.flatMap((g) => g.names);
  const names = q
    ? all.filter((n) => n.toLowerCase().includes(q)).slice(0, 6)
    : groups
        .filter((g) => g.key !== 'rest')
        .flatMap((g) => g.names)
        .slice(0, 4);
  const pick = names[Math.min(at, names.length - 1)];
  const submit = (to = pick) => to && edit.mutate({ task: t.id, owner: to }, { onSuccess: onClose });
  return (
    <Sheet
      label={`Assign ${taskRef(t.id)} to…`}
      submit={pick ? `Assign to ${pick}` : 'Pick someone'}
      onCancel={onClose}
      onSubmit={submit}
      busy={edit.isPending}
    >
      <div className="grid gap-2">
        <Input
          type="text"
          role="combobox"
          aria-expanded
          aria-controls="assign-list"
          aria-label="Find someone"
          placeholder="Find someone"
          value={find}
          onChange={(e) => (setFind(e.target.value), setAt(0))}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              submit();
            } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              e.preventDefault();
              setAt((i) => (i + (e.key === 'ArrowDown' ? 1 : names.length - 1)) % Math.max(names.length, 1));
            }
          }}
        />
        <ul id="assign-list" className="m-0 grid list-none gap-0.5 p-0" role="listbox" aria-label={q ? 'Matches' : 'Likely'}>
          {names.map((n) => (
            <li key={n} role="option" aria-selected={n === pick}>
              <button
                type="button"
                className={`w-full cursor-pointer rounded-chip border-0 px-2 py-1 text-left text-body text-ink ${n === pick ? 'bg-raised' : 'bg-transparent'}`}
                onClick={() => submit(n)}
              >
                {n}
              </button>
            </li>
          ))}
        </ul>
        {!names.length ? <span className="muted">{q ? 'No one matches.' : 'There is no one else to assign it to.'}</span> : null}
        {edit.isError ? (
          <span className="tsheet-err text-bad" role="alert">
            {sentence(classify(edit.error))}
          </span>
        ) : null}
      </div>
    </Sheet>
  );
}
