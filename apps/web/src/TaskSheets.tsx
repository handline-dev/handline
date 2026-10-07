// The small sheets of the Tasks list: owner, states, priority, merge and the key sheet.
import { useState } from 'react';
import { CLOSED, LISTED, TASK_STATES, type TaskFilter } from '@handline/app-core';
import { Sheet } from './taskui.tsx';

/** The bridge's twelve keys (`TasksView.KEYS`) plus the web's `n`, for the key sheet. */
export const LIST_KEYS: [string, string][] = [
  ['Space', 'mark or unmark the row'],
  ['A', 'mark every row shown, or unmark them'],
  ['Enter', "open the task's detail"],
  ['d', 'discard, with a reason'],
  ['o', 'change the owner to another agent'],
  ['p', 'set the priority, 1 high to 3 low'],
  ['m', 'fold the marked tasks into the row under the cursor'],
  ['s / S', 'sort by the next column / reverse'],
  ['a', 'show or hide done and discarded tasks'],
  ['v', 'pick which states to show'],
  ['t', 'switch between the list and the tree'],
  ['f', "show the next agent's tasks"],
  ['n', 'new task'],
  ['j k ↑ ↓', 'move the cursor'],
];

export function OwnerSheet({ agents, onCancel, onPick }: { agents: string[]; onCancel: () => void; onPick: (owner: string) => void }) {
  const [owner, setOwner] = useState('');
  return (
    <Sheet label="new owner (agent name):" submit="Change owner" onCancel={onCancel} onSubmit={() => owner && onPick(owner)}>
      <select value={owner} aria-label="New owner" onChange={(e) => setOwner(e.target.value)}>
        <option value="">pick an agent</option>
        {agents.map((a) => (
          <option key={a}>{a}</option>
        ))}
      </select>
    </Sheet>
  );
}

export function StatesSheet({ filter, onChange, onClose }: { filter: TaskFilter; onChange: (f: TaskFilter) => void; onClose: () => void }) {
  return (
    <Sheet label="show tasks in state" submit="Apply" onCancel={onClose} onSubmit={onClose}>
      {TASK_STATES.filter((s) => !CLOSED.includes(s)).map((s) => (
        <label key={s} className="tcheck">
          <input
            type="checkbox"
            checked={filter.states.has(s)}
            onChange={(e) => {
              const states = new Set(filter.states);
              if (e.target.checked) states.add(s);
              else states.delete(s);
              onChange({ ...filter, states });
            }}
          />{' '}
          {s}
        </label>
      ))}
      <button type="button" className="btn" onClick={() => onChange({ ...filter, states: new Set(LISTED) })}>
        reset
      </button>
    </Sheet>
  );
}

export function PrioritySheet({ onCancel, onPick }: { onCancel: () => void; onPick: (p: 1 | 2 | 3) => void }) {
  return (
    <Sheet label="priority (1 high, 2, 3 low):" submit="Set" onCancel={onCancel} onSubmit={onCancel}>
      {([1, 2, 3] as const).map((p) => (
        <button key={p} type="button" className="btn" onClick={() => onPick(p)}>
          {p}
          {p === 1 ? ' high' : p === 3 ? ' low' : ''}
        </button>
      ))}
    </Sheet>
  );
}

export function KeysSheet({ onClose }: { onClose: () => void }) {
  return (
    <Sheet label="Keys on this page" submit="Close" onCancel={onClose} onSubmit={onClose}>
      <dl className="keysheet">
        {LIST_KEYS.map(([k, v]) => (
          <div key={k}>
            <dt>
              <kbd>{k}</kbd>
            </dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </Sheet>
  );
}
