// Handing a task to someone, with what to tell them.
import { useState } from 'react';
import { assignGroups, taskRef } from '@handline/app-core';
import type { TaskDetail as Detail } from '@handline/client';
import { classify, sentence, useAgents, useTeam, useWrite } from '@handline/app-data';
import { Input, Popup } from '#src/ui/index.ts';

export const CHIP = 'rounded-full border px-2 py-0.5 text-sm';
export const chipLook = (on: boolean) => (on ? 'border-acc bg-acc text-on-acc' : 'border-line-strong bg-transparent text-ink tint');

const SHOWN = 5;

/**
 * One small popover from the Assign button (or the Owner chip): a search and a short list of who is likely, Me and the owner now. A click or Enter
 * hands the task over at once and closes it; Escape or a click outside closes it. It is drawn over the page, so the bar keeps its height.
 */
export function AssignPopover({ t, me, open, onClose, up }: { t: Detail; me: string; open: boolean; onClose: () => void; up?: boolean }) {
  return (
    <Popup open={open} onClose={onClose} title={`Assign ${taskRef(t.id)} to`} up={up} bare align="left">
      <AssignList t={t} me={me} onClose={onClose} />
    </Popup>
  );
}

function AssignList({ t, me, onClose }: { t: Detail; me: string; onClose: () => void }) {
  const edit = useWrite('task.edit');
  const known = useAgents().data?.agents.map((a) => a.name) ?? [];
  const team = useTeam().data ?? { agents: [] };
  const groups = assignGroups(t, team, known, me);
  const likely = groups.filter((g) => g.key !== 'rest').flatMap((g) => g.names);
  const everyone = [...new Set([...groups.flatMap((g) => g.names), me, t.to])];
  const [find, setFind] = useState('');
  const [at, setAt] = useState(0);
  const q = find.trim().toLowerCase();
  const short = [...new Set([...likely.slice(0, SHOWN - 2), me, t.to])];
  const names = q ? everyone.filter((n) => n.toLowerCase().includes(q) || (n === me && 'me'.includes(q))).slice(0, 6) : short;
  const pick = names[Math.min(at, names.length - 1)];
  const submit = (to = pick) => {
    if (!to) return;
    if (to === t.to) return onClose();
    edit.mutate({ task: t.id, owner: to }, { onSuccess: onClose });
  };
  return (
    <div className="grid w-[300px] max-w-[90vw] gap-1 p-1.5">
      <Input
        type="text"
        role="combobox"
        aria-expanded
        aria-controls="assign-list"
        aria-label="Find someone"
        placeholder="Find someone"
        autoFocus
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
              className={`flex w-full cursor-pointer items-center justify-between gap-2 rounded-sm border-0 px-2 py-1 text-left text-md text-ink ${n === pick ? 'bg-raised' : 'bg-transparent'}`}
              onClick={() => submit(n)}
            >
              <span className="truncate">{n === me ? 'Me' : n}</span>
              <span className="flex-none text-xs text-ink-3">{n === t.to ? 'now' : n === pick ? '↵' : ''}</span>
            </button>
          </li>
        ))}
      </ul>
      {!names.length ? <span className="muted px-2">{q ? 'No one matches.' : 'There is no one else to assign it to.'}</span> : null}
      {!q && everyone.length > names.length ? (
        <span className="px-2 text-xs text-ink-3">{`Type to find more · ${everyone.length} in all`}</span>
      ) : null}
      {edit.isError ? (
        <span className="tsheet-err px-2 text-bad" role="alert">
          {sentence(classify(edit.error))}
        </span>
      ) : null}
    </div>
  );
}
